# S3 스토리지 클래스 & Lifecycle 자동화

## 1. 개요

S3는 데이터 접근 패턴에 따라 비용이 크게 다른 여러 스토리지 클래스를 제공한다.
Lifecycle 정책으로 시간이 지남에 따라 저렴한 클래스로 자동 이동시키고,
Intelligent-Tiering으로 접근 빈도를 AWS가 자동으로 판단하게 할 수 있다.
올바른 설정만으로 S3 비용을 30~70% 절감 가능하다.

---

## 2. 설명

### 2.1 핵심 개념

**S3 스토리지 클래스 비교표 (ap-northeast-2 기준)**

| 클래스 | GB당 비용 | 검색 비용 | 최소 보관 기간 | 검색 시간 | 주요 용도 |
|--------|---------|---------|-------------|---------|---------|
| Standard | $0.025 | 없음 | 없음 | 즉시 | 자주 접근하는 데이터 |
| Standard-IA | $0.0138 | $0.01/GB | 30일 | 즉시 | 월 1회 미만 접근 |
| One Zone-IA | $0.011 | $0.01/GB | 30일 | 즉시 | 재생성 가능 데이터 |
| Glacier Instant | $0.005 | $0.03/GB | 90일 | 즉시 | 분기 1회 접근 |
| Glacier Flexible | $0.0045 | $0.01/GB + 검색 | 90일 | 분~시간 | 연 1회 접근 |
| Glacier Deep Archive | $0.00099 | $0.02/GB + 검색 | 180일 | 12시간 | 7년 이상 보관 규정 준수 |

**Intelligent-Tiering 동작 원리**

```
업로드 후 처음에는 Frequent Access 티어에 저장
    ↓ 30일 동안 접근 없으면
Infrequent Access 티어로 자동 이동 (45% 비용 절감)
    ↓ 90일 동안 접근 없으면 (선택적 활성화)
Archive Instant Access 티어 (68% 절감)
    ↓ 180일 동안 접근 없으면 (선택적 활성화)
Deep Archive Access 티어 (95% 절감)

접근 시 → 즉시 Frequent Access 티어로 복귀
```

**Intelligent-Tiering 주의사항**
- 128KB 미만 객체는 Infrequent Access로 이동하지 않고 항상 Frequent Access에 머뭄
- 모니터링 비용: $0.0025/1,000 objects/월 → 수백만 개의 소파일은 비용 증가 가능
- 파일 수가 매우 많고 개별 파일이 작으면 Lifecycle 정책이 더 효율적

---

### 2.2 실무 적용 코드

**Terraform — Lifecycle 정책 (로그 데이터 패턴)**

```hcl
resource "aws_s3_bucket_lifecycle_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id

  # 규칙 1: 애플리케이션 로그
  rule {
    id     = "app-logs-lifecycle"
    status = "Enabled"

    filter {
      prefix = "app-logs/"    # 특정 접두사에만 적용
    }

    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }

    transition {
      days          = 90
      storage_class = "GLACIER_INSTANT_RETRIEVAL"
    }

    transition {
      days          = 365
      storage_class = "DEEP_ARCHIVE"
    }

    expiration {
      days = 2557    # 7년 후 삭제 (규정 준수)
    }
  }

  # 규칙 2: 백업 데이터
  rule {
    id     = "backup-lifecycle"
    status = "Enabled"

    filter {
      prefix = "backups/"
    }

    transition {
      days          = 7
      storage_class = "STANDARD_IA"    # 1주일 후 IA로
    }

    transition {
      days          = 30
      storage_class = "GLACIER_INSTANT_RETRIEVAL"
    }

    expiration {
      days = 365   # 1년 후 삭제
    }
  }

  # 규칙 3: 불완전 멀티파트 업로드 정리 (비용 절감)
  rule {
    id     = "abort-incomplete-multipart"
    status = "Enabled"

    filter {}    # 버킷 전체 적용

    abort_incomplete_multipart_upload {
      days_after_initiation = 7   # 7일 내 완료 안 된 멀티파트 업로드 삭제
    }
  }

  # 규칙 4: 이전 버전 정리 (버전닝 활성화 버킷)
  rule {
    id     = "old-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_transition {
      noncurrent_days = 30
      storage_class   = "STANDARD_IA"
    }

    noncurrent_version_expiration {
      noncurrent_days = 90    # 90일 후 이전 버전 삭제
    }

    # 삭제 마커 정리
    expiration {
      expired_object_delete_marker = true
    }
  }
}
```

**Terraform — Intelligent-Tiering 활성화**

```hcl
resource "aws_s3_bucket_intelligent_tiering_configuration" "entire_bucket" {
  bucket = aws_s3_bucket.data.id
  name   = "entire-bucket"

  tiering {
    access_tier = "ARCHIVE_ACCESS"
    days        = 90    # 90일 미접근 시 Archive Instant로 이동
  }

  tiering {
    access_tier = "DEEP_ARCHIVE_ACCESS"
    days        = 180   # 180일 미접근 시 Deep Archive로 이동
  }
}

# 스토리지 클래스를 Intelligent-Tiering으로 업로드
resource "aws_s3_object" "config" {
  bucket        = aws_s3_bucket.data.id
  key           = "config/app.json"
  source        = "config/app.json"
  storage_class = "INTELLIGENT_TIERING"   # 업로드 시 명시
}
```

**AWS CLI — 기존 Standard 객체를 Intelligent-Tiering으로 일괄 전환**

```bash
# 특정 prefix의 모든 객체를 Intelligent-Tiering으로 변경
aws s3 cp s3://my-bucket/data/ s3://my-bucket/data/ \
  --recursive \
  --storage-class INTELLIGENT_TIERING \
  --metadata-directive COPY

# S3 Batch Operations를 사용하면 수백만 객체 일괄 처리 가능
# 1. S3 인벤토리로 객체 목록 생성 → 2. Batch Operations 작업 생성
```

**S3 Storage Lens — 사용 현황 대시보드**

```hcl
resource "aws_s3control_storage_lens_configuration" "example" {
  config_id  = "my-storage-lens"
  account_id = data.aws_caller_identity.current.account_id

  storage_lens_configuration {
    enabled = true

    account_level {
      bucket_level {}
      activity_metrics {
        enabled = true
      }
      cost_optimization_metrics {
        enabled = true
      }
    }

    # S3 버킷에 보고서 저장
    data_export {
      s3_bucket_destination {
        account_id  = data.aws_caller_identity.current.account_id
        arn         = aws_s3_bucket.storage_lens_reports.arn
        format      = "Parquet"
        output_schema_version = "V_1"
      }
    }
  }
}
```

---

### 2.3 보안/비용 Best Practice

- **불완전 멀티파트 업로드 정리 필수**: 매달 비용 청구됨에도 모르는 경우 많음 → 7일 정리 규칙 모든 버킷에 적용
- **이전 버전(Noncurrent) 관리**: 버전닝 활성화 버킷에서 이전 버전 무한 누적 방지
- **Glacier 조기 삭제 페널티 주의**: 90일(Glacier Instant), 90일(Glacier Flexible), 180일(Deep Archive) 내 삭제 시 잔여 기간 비용 청구
- **작은 파일 많은 경우 Lifecycle 우선**: 128KB 미만 소파일이 많으면 Intelligent-Tiering 모니터링 비용이 스토리지 비용 초과 가능

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**Lifecycle 정책 미적용**

```bash
# 정책 설정 확인
aws s3api get-bucket-lifecycle-configuration --bucket my-bucket

# 흔한 원인 1: 128KB 미만 객체는 Standard-IA로 이동 안 됨
# (AWS가 비용 효율성을 위해 자동 제외)

# 흔한 원인 2: 최소 보관 기간 미충족
# Standard → Standard-IA: 30일 후에 이동 가능

# 흔한 원인 3: 전환 후 즉시 다시 접근한 경우 (Intelligent-Tiering만 해당)
```

**Glacier에서 데이터 복구 지연**

```bash
# Glacier Flexible 복구 (3가지 속도 옵션)
aws s3api restore-object \
  --bucket my-bucket \
  --key archived/data.tar.gz \
  --restore-request '{
    "Days": 7,
    "GlacierJobParameters": {
      "Tier": "Standard"    # Expedited(1~5분), Standard(3~5시간), Bulk(5~12시간)
    }
  }'

# 복구 상태 확인
aws s3api head-object \
  --bucket my-bucket \
  --key archived/data.tar.gz \
  --query 'Restore'
# "ongoing-request=\"false\", expiry-date=\"Thu, 22 Jan 2024 00:00:00 GMT\""
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: S3 비용 청구서에서 이유 모를 PUT 요청 비용이 많습니다**
A: Lifecycle 전환 자체가 PUT 요청으로 과금됩니다. 전환 대상 객체 수 × 전환 규칙 수만큼 비용이 발생합니다. Standard-IA의 경우 $0.01/1,000 PUT 요청.

**Q: Intelligent-Tiering이 Standard보다 비싸게 나옵니다**
A: 소파일(128KB 미만)이 많거나, 전체 데이터가 자주 접근되는 경우 모니터링 비용이 절감 효과를 상회합니다. Storage Lens로 접근 패턴을 먼저 분석하세요.

---

## 4. 모니터링 및 알람

```hcl
# S3 버킷 크기별 스토리지 클래스 추적
resource "aws_cloudwatch_metric_alarm" "s3_cost_spike" {
  alarm_name          = "s3-bucket-size-spike"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "BucketSizeBytes"
  namespace           = "AWS/S3"
  period              = 86400   # 1일
  statistic           = "Average"
  threshold           = 1099511627776   # 1TB 초과 시 알람

  dimensions = {
    BucketName  = aws_s3_bucket.logs.id
    StorageType = "StandardStorage"
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
}
```

**S3 Storage Lens 주요 지표**

| 지표 | 활용 |
|------|------|
| `StorageSizeBytes` | 스토리지 클래스별 용량 |
| `ObjectCount` | 클래스별 객체 수 |
| `IncompleteMultipartUploadCount` | 정리 대상 불완전 업로드 수 |
| `NonCurrentVersionStorageBytes` | 이전 버전 사용 용량 |

---

## 5. TIP

- **AWS Cost Explorer S3 분석**: 스토리지 클래스별, 버킷별 비용을 세분화해서 최적화 우선순위 파악
- **S3 Inventory**: 대용량 버킷의 객체 목록과 스토리지 클래스를 CSV/ORC로 주기적으로 내보내 분석 가능 (Athena와 연계)
- **보관 규정 준수**: 금융/의료 데이터는 삭제 금지 기간이 있으면 S3 Object Lock + Glacier Deep Archive 조합으로 WORM (Write Once Read Many) 구현
