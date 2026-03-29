# Route 53 장애 조치 라우팅 (Failover Routing)

## 1. 개요

Route 53 Failover 라우팅은 Primary 리소스가 비정상 상태가 되면 자동으로 Secondary 리소스로 트래픽을 전환하는 기능이다.
헬스체크와 연동하여 수분 내에 자동 전환이 이루어지며,
DNS TTL을 짧게 설정할수록 전환이 빠르다.

---

## 2. 설명

### 2.1 핵심 개념

**Route 53 라우팅 정책 종류**

| 정책 | 동작 | 주요 용도 |
|------|------|---------|
| Simple | 단일 리소스 | 기본 |
| Weighted | 비율로 분배 | A/B 테스트, 점진적 마이그레이션 |
| Latency | 가장 낮은 지연 리전 | 멀티 리전 서비스 |
| **Failover** | Primary 비정상 시 Secondary로 전환 | DR, 고가용성 |
| Geolocation | 사용자 위치 기반 | 지역별 콘텐츠 서비스 |
| Multivalue | 여러 IP 랜덤 반환 | 간단한 로드밸런싱 |

**Failover 동작 흐름**

```
정상 상태:
  DNS 조회 → Primary 레코드 반환 (ALB, EIP 등)

Primary 헬스체크 실패 (N회 연속):
  Route 53가 Primary 레코드 비활성화
  DNS 조회 → Secondary 레코드 반환 (다른 리전 ALB, S3 정적 페이지 등)

Primary 복구 후:
  헬스체크 성공 (N회 연속)
  DNS 조회 → Primary 레코드 복귀
```

**헬스체크 유형**

| 유형 | 설명 |
|------|------|
| HTTP/HTTPS | 엔드포인트에 HTTP 요청, 2xx/3xx 응답 확인 |
| TCP | TCP 포트 연결 가능 여부 확인 |
| Calculated | 여러 헬스체크 결과를 AND/OR로 조합 |
| CloudWatch Alarm | CloudWatch 알람 상태 기반 (Private 리소스에 유용) |

---

### 2.2 실무 적용 코드

**Terraform — 헬스체크 + Failover 레코드 생성**

```hcl
# Primary 엔드포인트 헬스체크
resource "aws_route53_health_check" "primary" {
  fqdn              = "api.primary.example.com"   # 또는 IP 직접 지정
  port              = 443
  type              = "HTTPS"
  resource_path     = "/health"                    # 헬스체크 경로
  failure_threshold = 3                            # 3회 연속 실패 시 비정상
  request_interval  = 30                           # 30초 간격 (10초 옵션도 있음)

  # SNI (HTTPS 필수)
  enable_sni = true

  # 알람 연동
  cloudwatch_alarm_region = "us-east-1"

  tags = {
    Name = "primary-health-check"
  }
}

# Hosted Zone 데이터 소스
data "aws_route53_zone" "main" {
  name         = "example.com."
  private_zone = false
}

# Primary 레코드 (Failover: PRIMARY)
resource "aws_route53_record" "primary" {
  zone_id = data.aws_route53_zone.main.zone_id
  name    = "api.example.com"
  type    = "A"
  ttl     = 60   # TTL은 장애 전환 목표 시간보다 짧게 설정

  failover_routing_policy {
    type = "PRIMARY"
  }

  health_check_id = aws_route53_health_check.primary.id
  set_identifier  = "primary"

  records = [aws_eip.primary.public_ip]
}

# Secondary 레코드 (Failover: SECONDARY) — ALB 예시
resource "aws_route53_record" "secondary" {
  zone_id = data.aws_route53_zone.main.zone_id
  name    = "api.example.com"
  type    = "A"
  ttl     = 60

  failover_routing_policy {
    type = "SECONDARY"
  }

  set_identifier = "secondary"

  # Secondary는 헬스체크 없어도 됨 (항상 반환)
  # 단, Secondary에도 헬스체크를 달면 둘 다 비정상 시 아무것도 반환 안 함
  records = [aws_eip.secondary.public_ip]
}
```

**Terraform — ALB Alias 레코드 (Failover + ALB 조합)**

```hcl
# Primary ALB (ap-northeast-2)
resource "aws_route53_record" "primary_alb" {
  zone_id = data.aws_route53_zone.main.zone_id
  name    = "api.example.com"
  type    = "A"

  failover_routing_policy {
    type = "PRIMARY"
  }

  set_identifier  = "primary-alb"
  health_check_id = aws_route53_health_check.primary_alb.id

  alias {
    name                   = aws_lb.primary.dns_name
    zone_id                = aws_lb.primary.zone_id
    evaluate_target_health = true   # ALB 자체 헬스체크도 반영
  }
}

# Secondary: S3 Static Website (장애 시 유지보수 페이지)
resource "aws_route53_record" "secondary_s3" {
  zone_id = data.aws_route53_zone.main.zone_id
  name    = "api.example.com"
  type    = "A"

  failover_routing_policy {
    type = "SECONDARY"
  }

  set_identifier = "secondary-s3"

  alias {
    name                   = aws_s3_bucket_website_configuration.maintenance.website_endpoint
    zone_id                = "Z3GKZC51ZEQ42"   # S3 Website용 고정 Zone ID
    evaluate_target_health = false
  }
}
```

**CloudWatch Alarm 기반 헬스체크 (Private 리소스)**

```hcl
# Private 서브넷 내 RDS 헬스 → CloudWatch Alarm → Route 53 헬스체크
resource "aws_cloudwatch_metric_alarm" "rds_healthy" {
  alarm_name          = "rds-primary-healthy"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  metric_name         = "DatabaseConnections"
  namespace           = "AWS/RDS"
  period              = 60
  statistic           = "Average"
  threshold           = 1   # 연결 수가 1 미만이면 비정상
  treat_missing_data  = "breaching"

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.primary.id
  }
}

resource "aws_route53_health_check" "rds_via_alarm" {
  type                            = "CLOUDWATCH_METRIC"
  cloudwatch_alarm_name           = aws_cloudwatch_metric_alarm.rds_healthy.alarm_name
  cloudwatch_alarm_region         = var.region
  insufficient_data_health_status = "Unhealthy"
}
```

**Active-Active (Weighted) vs Active-Passive (Failover) 비교**

```hcl
# Active-Active: Weighted 라우팅 (두 리전 모두 트래픽 처리)
resource "aws_route53_record" "region1" {
  zone_id = data.aws_route53_zone.main.zone_id
  name    = "api.example.com"
  type    = "A"

  weighted_routing_policy {
    weight = 50   # 50% 트래픽
  }

  set_identifier  = "region1"
  health_check_id = aws_route53_health_check.region1.id

  alias {
    name                   = aws_lb.region1.dns_name
    zone_id                = aws_lb.region1.zone_id
    evaluate_target_health = true
  }
}

# Active-Passive: Failover 라우팅 (Primary 비정상 시에만 Secondary 사용)
# → 위 Failover 예시 참고
```

---

### 2.3 보안/비용 Best Practice

- **TTL은 전환 목표 시간보다 짧게**: 목표 RTO 5분이면 TTL 60초 설정 (캐시된 DNS 레코드 고려)
- **Primary 헬스체크 임계값**: `failure_threshold = 3` + `request_interval = 30` = 최대 90초 후 전환
- **Secondary에도 헬스체크 권장**: 두 엔드포인트 모두 비정상일 때 NXDOMAIN 반환보다 Secondary 그대로 반환이 나은 경우도 있음 (유지보수 페이지)
- **Route 53 ARC (Application Recovery Controller)**: 멀티 리전 대규모 애플리케이션의 전환 조작을 안전하게 제어하는 관리형 서비스

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**Failover 전환 지연**

```bash
# 현재 헬스체크 상태 확인
aws route53 get-health-check-status \
  --health-check-id <health-check-id> \
  --query 'HealthCheckObservations[*].{Region:Region,Status:StatusReport.Status}'

# 헬스체크가 글로벌 여러 리전에서 확인함 (대부분의 리전이 실패해야 전환)
# 지연 원인 1: failure_threshold가 높음 → 낮추기
# 지연 원인 2: TTL이 길어서 클라이언트 DNS 캐시 유지 → TTL 단축

# DNS 전파 확인
dig @8.8.8.8 api.example.com   # Google DNS
dig @1.1.1.1 api.example.com   # Cloudflare DNS
```

**Secondary로 전환 후 Primary 복구 시 원복 안 됨**

```bash
# 헬스체크 상태 확인 (Primary가 정말 Healthy로 돌아왔는지)
aws route53 get-health-check-status \
  --health-check-id <primary-health-check-id>

# Primary 레코드가 여전히 비활성 상태인 경우
# Route 53는 헬스체크 성공 후 자동으로 Primary 복귀
# 단, request_interval과 failure_threshold에 따라 복귀에도 시간 소요

# 강제 복귀 방법: 헬스체크 임시 비활성화 후 재활성화
aws route53 update-health-check \
  --health-check-id <id> \
  --disabled     # 일시 비활성화 → Healthy로 즉시 인식
```

**Private Hosted Zone 헬스체크 제약**

```bash
# Private Hosted Zone의 레코드는 일반 HTTP/HTTPS 헬스체크 불가
# (Route 53 헬스체커가 인터넷에서 접근 불가)
# 해결: CloudWatch Alarm 기반 헬스체크 사용
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: TTL을 너무 짧게 설정하면 Route 53 비용이 증가하나요?**
A: 예. TTL이 짧으면 DNS 조회 빈도가 증가하여 쿼리 비용이 늘어납니다 ($0.40/백만 쿼리). 일반적으로 60~300초가 적당한 균형입니다.

**Q: ALB에 `evaluate_target_health = true`를 설정했는데 헬스체크가 너무 민감합니다**
A: ALB는 등록된 타겟 중 하나라도 Unhealthy면 ALB 자체가 Unhealthy로 판단됩니다. ALB 헬스체크 임계값과 Route 53 헬스체크 임계값을 모두 고려해야 합니다.

---

## 4. 모니터링 및 알람

```hcl
# 헬스체크 상태 변경 알람
resource "aws_cloudwatch_metric_alarm" "health_check_failed" {
  alarm_name          = "route53-primary-health-check-failed"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  metric_name         = "HealthCheckStatus"
  namespace           = "AWS/Route53"
  period              = 60
  statistic           = "Minimum"
  threshold           = 1   # 0 = Unhealthy, 1 = Healthy

  dimensions = {
    HealthCheckId = aws_route53_health_check.primary.id
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
}

# Failover 발생 감지 (CloudTrail로 레코드 변경 추적)
resource "aws_cloudwatch_event_rule" "route53_record_change" {
  name = "route53-failover-triggered"

  event_pattern = jsonencode({
    source      = ["aws.route53"]
    detail-type = ["AWS API Call via CloudTrail"]
    detail = {
      eventName = ["ChangeResourceRecordSets"]
    }
  })
}
```

---

## 5. TIP

- **DNS 전파 확인 도구**: [dnschecker.org](https://dnschecker.org) — 전 세계 여러 DNS 서버에서 레코드 전파 상태 실시간 확인
- **Route 53 ARC Zonal Shift**: ALB/NLB가 특정 AZ에서 문제 발생 시 해당 AZ 트래픽을 즉시 다른 AZ로 이동 (수초 내 전환, Failover보다 빠름)
- **헬스체크 비용**: $0.50/헬스체크/월 (일반), $1.00/헬스체크/월 (고속 — 10초 간격). 10초 간격은 빠른 전환이 필요할 때만 사용
- **멀티 리전 Active-Active**: Weighted + Latency 정책 조합으로 평소에는 지연 시간 기반 라우팅, 한 리전 장애 시 나머지로 자동 전환 가능
