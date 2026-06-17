# EFS Standard / IA 비용 Best Practice

## 1. 개요

Amazon EFS는 파일 단위로 Standard, Infrequent Access(IA), Archive 스토리지 클래스를 자동 전환할 수 있다.
Standard는 자주 접근하는 파일에 적합하고 지연 시간이 낮지만 저장 단가가 높다.
IA는 저장 단가를 낮추는 대신 파일 내용 접근, 쓰기, 티어링 활동에 따른 비용과 더 높은 지연 시간을 고려해야 한다.

실무에서 자주 발생하는 비용 사고는 다음 패턴이다.

```text
대부분의 파일이 IA에 있어 비용이 낮음
    ↓
lifecycle 정책에서 "첫 접근 시 Standard 복귀" 활성화
    ↓
배치, 백업, 인덱싱, 점검 스크립트가 대량 파일을 읽음
    ↓
대량 파일이 Standard로 복귀
    ↓
다음 청구서에서 Standard 저장 비용 급증
```

IA를 쓰다가 Standard로 넘어오면 비용 차이가 크게 보이는 것이 정상이다.
EFS 비용 관리는 "저장 용량 단가"만 보면 안 되고, 어떤 작업이 파일을 다시 Standard로 끌어올리는지까지 같이 봐야 한다.

---

## 2. 핵심 개념

### 2.1 Standard와 IA의 차이

| 항목 | EFS Standard | EFS IA |
|------|--------------|--------|
| 목적 | 자주 접근하는 운영 데이터 | 분기 몇 회 수준으로 접근하는 데이터 |
| 지연 시간 | 가장 낮음 | Standard보다 높음 |
| 저장 단가 | 높음 | 낮음 |
| 접근 비용 | 상대적으로 단순 | 읽기/쓰기/티어링 활동 비용 고려 필요 |
| 적합한 데이터 | active working set, 업로드 직후 처리 데이터, hot config | 오래된 첨부파일, 과거 로그, 낮은 빈도의 공유 데이터 |
| 주의점 | 용량이 커지면 비용이 빠르게 증가 | 재접근이 많으면 절감 효과가 줄어듦 |

EFS IA는 "싸게 저장하는 티어"이지 "자주 읽어도 항상 싼 티어"가 아니다.
읽기 패턴이 반복되거나 대량 스캔이 있으면 IA 접근 비용과 Standard 복귀 비용을 같이 계산해야 한다.

### 2.2 lifecycle 정책의 함정

EFS lifecycle은 파일시스템 전체에 적용된다.
파일별, 디렉터리별로 다른 lifecycle을 줄 수 없으므로 하나의 EFS에 hot/cold 데이터가 섞여 있으면 비용 예측이 어려워진다.

주요 정책:

| 정책 | 의미 | 비용 관점 |
|------|------|-----------|
| `transition_to_ia` | Standard에서 일정 기간 미접근 파일을 IA로 이동 | 보통 30일 이상부터 검토 |
| `transition_to_archive` | Standard 또는 IA에서 장기 미접근 파일을 Archive로 이동 | 거의 읽지 않는 장기 보관 데이터용 |
| `transition_to_primary_storage_class` | IA/Archive 파일 접근 시 Standard로 복귀 | 성능은 좋아지지만 비용 급증 원인이 될 수 있음 |

`transition_to_primary_storage_class = "AFTER_1_ACCESS"`는 성능 민감 워크로드에서는 유용하다.
하지만 운영 데이터 전체를 한 번 훑는 작업이 있으면 다량의 IA 파일이 Standard로 돌아온다.
사용자가 "IA를 쓰다가 Standard로 넘어오니 비용 차이가 엄청 심하다"고 느끼는 대표 원인이다.

---

## 3. 권장 설정 기준

### 3.1 기본 권장안

```hcl
resource "aws_efs_file_system" "main" {
  creation_token  = "app-shared-efs"
  encrypted       = true
  throughput_mode = "elastic"

  lifecycle_policy {
    transition_to_ia = "AFTER_30_DAYS"
  }

  # 기본적으로 IA/Archive 접근 후 Standard 자동 복귀는 끈다.
  # 성능상 반드시 필요한 파일시스템에만 별도로 활성화한다.
  # lifecycle_policy {
  #   transition_to_primary_storage_class = "AFTER_1_ACCESS"
  # }

  tags = {
    Name = "app-shared-efs"
  }
}
```

운영 기본값:

- `transition_to_ia`: 30일 또는 60일 미접근부터 시작
- `transition_to_primary_storage_class`: 기본 비활성 권장
- `throughput_mode`: 예측이 어렵거나 burst성 워크로드는 Elastic 우선
- `performance_mode`: 대부분 General Purpose 권장
- EFS 하나에 hot/cold 데이터를 섞지 말고 파일시스템 또는 경로 구조를 분리

### 3.2 Standard 자동 복귀를 켜도 되는 경우

다음 조건을 만족할 때만 `AFTER_1_ACCESS`를 검토한다.

- 사용자 요청 경로에서 IA 지연 시간이 직접 체감된다.
- 같은 파일을 한 번 읽은 뒤 짧은 기간 동안 반복해서 읽는다.
- 대량 스캔, 백업, 인덱싱, 바이러스 검사, `cat`, `rsync`, 콘텐츠 기반 `find` 작업이 통제되어 있다.
- CloudWatch로 Standard/IA 용량 변화와 비용 알림을 이미 보고 있다.

반대로 다음 경우에는 켜지 않는다.

- 대부분의 파일이 오래된 첨부파일, 아카이브성 데이터다.
- 월 1회 이상 전체 파일 검증, 백업 검증, 검색 인덱싱을 수행한다.
- 사용자가 직접 파일 전체를 훑는 분석 작업을 한다.
- EFS 비용 알림 없이 월말 청구서로만 확인한다.

### 3.3 hot/cold 분리 패턴

```text
권장: 파일시스템 분리

efs-hot
  - Standard 위주
  - 업로드 직후 처리, 자주 읽는 파일
  - transition_to_primary_storage_class 사용 가능

efs-cold
  - IA/Archive 위주
  - 오래된 첨부파일, 보관 로그, 낮은 빈도 데이터
  - Standard 자동 복귀 비활성
```

EFS lifecycle은 파일시스템 전체에 적용되므로 비용 정책이 다른 데이터를 같은 EFS에 넣지 않는 것이 가장 단순하다.
EKS에서는 StorageClass를 hot/cold로 나누고, 애플리케이션 PVC도 목적별로 분리한다.

---

## 4. 비용 점검 명령

### 4.1 Standard / IA / Archive 용량 확인

```bash
FILE_SYSTEM_ID="fs-xxxxxxxxxxxxxxxxx"

aws efs describe-file-systems \
  --file-system-id "${FILE_SYSTEM_ID}" \
  --query 'FileSystems[0].SizeInBytes' \
  --output table
```

확인할 값:

- `ValueInStandard`: Standard에 남아 있는 용량
- `ValueInIA`: IA에 있는 용량
- `ValueInArchive`: Archive에 있는 용량
- `Value`: 전체 metered size

Standard 비용이 갑자기 증가했다면 `ValueInStandard`가 언제부터 늘었는지 CloudWatch 지표와 Cost Explorer에서 같이 본다.

### 4.2 lifecycle 설정 확인

```bash
aws efs describe-lifecycle-configuration \
  --file-system-id "${FILE_SYSTEM_ID}" \
  --output table
```

`TransitionToPrimaryStorageClass=AFTER_1_ACCESS`가 있으면 IA/Archive 파일이 접근될 때 Standard로 복귀할 수 있다.
비용 급증을 조사할 때 가장 먼저 확인한다.

### 4.3 Standard 자동 복귀 제거

```bash
aws efs put-lifecycle-configuration \
  --file-system-id "${FILE_SYSTEM_ID}" \
  --lifecycle-policies TransitionToIA=AFTER_30_DAYS
```

주의:

- 위 명령은 lifecycle 정책 전체를 새 값으로 교체한다.
- Archive 정책을 쓰고 있다면 함께 명시해야 한다.
- 이미 Standard로 돌아온 파일은 즉시 IA로 내려가지 않는다.
- IA 전환은 lifecycle 백그라운드 작업으로 처리되며, 파일 수와 워크로드에 따라 시간이 걸린다.

Archive도 함께 쓰는 예시:

```bash
aws efs put-lifecycle-configuration \
  --file-system-id "${FILE_SYSTEM_ID}" \
  --lifecycle-policies \
    TransitionToIA=AFTER_30_DAYS \
    TransitionToArchive=AFTER_90_DAYS
```

---

## 5. 비용 사고 대응 Runbook

### 5.1 증상

- EFS 비용이 갑자기 증가
- Cost Explorer에서 EFS Standard 저장 비용 비중 증가
- IA 사용량이 줄고 Standard 사용량이 증가
- 최근 배치, 백업, 마이그레이션, 인덱싱 작업 이후 비용 증가

### 5.2 즉시 확인

```bash
FILE_SYSTEM_ID="fs-xxxxxxxxxxxxxxxxx"

# 현재 스토리지 클래스별 용량
aws efs describe-file-systems \
  --file-system-id "${FILE_SYSTEM_ID}" \
  --query 'FileSystems[0].SizeInBytes'

# lifecycle 정책
aws efs describe-lifecycle-configuration \
  --file-system-id "${FILE_SYSTEM_ID}"

# 마운트 타겟과 접근 경로 확인
aws efs describe-mount-targets \
  --file-system-id "${FILE_SYSTEM_ID}" \
  --query 'MountTargets[*].[MountTargetId,SubnetId,LifeCycleState,IpAddress]' \
  --output table
```

확인 질문:

- `TransitionToPrimaryStorageClass=AFTER_1_ACCESS`가 켜져 있었는가?
- 최근 전체 파일을 읽는 작업이 있었는가?
- 백업 도구가 파일 내용까지 읽었는가?
- `rsync`, `cp`, 검색 인덱싱, 썸네일 재생성, 백신 스캔이 실행됐는가?
- 애플리케이션 릴리스 후 cold 파일 접근 패턴이 바뀌었는가?

### 5.3 조치

```bash
# 1. Standard 자동 복귀 제거
aws efs put-lifecycle-configuration \
  --file-system-id "${FILE_SYSTEM_ID}" \
  --lifecycle-policies TransitionToIA=AFTER_30_DAYS

# 2. 대량 파일 읽기 작업 중지 또는 범위 축소
# 예: 전체 스캔 대신 최근 N일 prefix/path만 처리하도록 변경

# 3. Standard 용량 변화 추적
watch -n 300 "aws efs describe-file-systems --file-system-id ${FILE_SYSTEM_ID} --query 'FileSystems[0].SizeInBytes'"
```

조치 후에도 비용은 즉시 내려가지 않을 수 있다.
이미 Standard로 복귀한 파일은 lifecycle 조건을 다시 만족하고 백그라운드 전환이 완료되어야 IA로 내려간다.

---

## 6. 모니터링 Best Practice

### 6.1 CloudWatch에서 볼 지표

- `StorageBytes`: `StorageClass=Standard`, `IA`, `Archive` 차원으로 추적
- `MeteredIOBytes`: 읽기/쓰기/메타데이터 IO 증가 확인
- `PercentIOLimit`: General Purpose 성능 한계 접근 여부 확인
- `BurstCreditBalance`: Bursting throughput 사용 시 크레딧 고갈 확인
- `PermittedThroughput`: 처리 가능한 throughput 변화 확인

### 6.2 비용 알림

- AWS Budgets에서 EFS 월 비용 예산 설정
- Cost Anomaly Detection에서 EFS 서비스 단위 이상 비용 알림 설정
- Cost Explorer에서 `Usage type` 기준으로 Standard, IA, IA access/tiering 비용 분리
- 태그 기준으로 서비스/팀/환경별 EFS 비용 분리

### 6.3 운영 규칙

- lifecycle 변경은 PR 또는 변경 승인으로 관리한다.
- `transition_to_primary_storage_class` 활성화는 비용 영향 리뷰를 필수로 한다.
- 전체 파일 읽기 작업은 사전에 대상 경로와 예상 읽기량을 계산한다.
- `du`, `find`, `ls` 같은 메타데이터 중심 작업과 파일 내용 읽기 작업을 구분한다.
- 새 배치 작업은 운영 EFS 전체를 대상으로 돌리기 전에 샘플 경로에서 IO량을 측정한다.

---

## 7. 의사결정표

| 상황 | 권장 선택 |
|------|-----------|
| 자주 읽고 쓰는 active 데이터 | Standard |
| 30일 이상 거의 읽지 않는 파일 | IA |
| 연 몇 회 이하로만 읽는 장기 보관 파일 | Archive 검토 |
| 접근하면 바로 빠른 성능이 필요한 cold 파일 | IA + `AFTER_1_ACCESS` 신중 검토 |
| 전체 스캔이 주기적으로 도는 파일시스템 | `AFTER_1_ACCESS` 비활성 |
| 비용 정책이 다른 데이터가 섞임 | EFS 분리 |
| EKS 여러 앱이 공유 | Access Point + PVC/StorageClass 분리 |
| 비용 급증 조사 | `ValueInStandard`와 lifecycle 정책부터 확인 |

---

## 8. 참고 문서

- Amazon EFS pricing: https://aws.amazon.com/efs/pricing/
- Managing EFS lifecycle: https://docs.aws.amazon.com/efs/latest/ug/lifecycle-management-efs.html
- Amazon EFS performance specifications: https://docs.aws.amazon.com/efs/latest/ug/performance.html
