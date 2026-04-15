# AWS CLI 동작 원리

## 1. 개요

AWS CLI는 **Python 기반**의 오픈소스 도구입니다. 내부적으로 Python AWS SDK인 **botocore**를 사용하며, 사용자가 입력한 명령어를 AWS REST API 호출로 변환하여 실행합니다.

운영 중 CLI가 예상과 다르게 동작하거나, SDK(boto3)와 동작 차이가 생길 때 내부 구조를 이해하면 원인을 빠르게 찾을 수 있습니다.

---

## 2. 설명

### 2.1 핵심 구조

```
사용자 입력
    │
    ▼
┌─────────────────────────────────────────────┐
│               AWS CLI (awscli)              │  ← 명령어 파싱, 출력 포맷팅
│  aws ec2 describe-instances --output json   │
└──────────────────┬──────────────────────────┘
                   │ 내부 호출
                   ▼
┌─────────────────────────────────────────────┐
│               botocore                      │  ← API 호출 엔진 (CLI·boto3 공통)
│  - 서비스 모델(JSON) 로딩                    │
│  - 자격 증명 해석                            │
│  - Signature V4 서명                        │
│  - HTTP 요청 전송 (urllib3)                  │
│  - 응답 파싱 및 에러 처리                    │
└──────────────────┬──────────────────────────┘
                   │ HTTPS 요청
                   ▼
        AWS API Endpoint
   (ec2.ap-northeast-2.amazonaws.com)
```

| 레이어 | 역할 |
|--------|------|
| **awscli** | 명령어 파싱, 출력 포맷(json/table/text/yaml), 페이지네이션 자동화 |
| **botocore** | 자격 증명 로딩, SigV4 서명, HTTP 전송, 재시도 로직, 에러 파싱 |
| **urllib3** | 실제 TCP 연결 및 HTTPS 통신 |

> boto3(Python SDK)도 botocore를 공유합니다. CLI와 boto3의 동작 차이는 대부분 awscli 레이어(파싱/포맷)에서 발생하며, API 호출 자체는 동일합니다.

---

### 2.2 CLI v1 vs CLI v2

| 항목 | CLI v1 | CLI v2 |
|------|--------|--------|
| 배포 방식 | `pip install awscli` (시스템 Python 의존) | 번들 Python 포함 독립 설치 파일 |
| Python 버전 | 시스템 Python 사용 | 내장 Python (사용자 환경 무관) |
| 설치 위치 | `/usr/local/bin/aws` (pip 경로) | `/usr/local/aws-cli/aws` |
| SSO 지원 | 제한적 | `aws sso login` 내장 지원 |
| 출력 포맷 | json/text/table | json/text/table/**yaml**/yaml-stream 추가 |
| 바이너리 처리 | Base64 수동 인코딩 필요 | 자동 처리 |
| 권장 여부 | 지원 종료 예정 | **현재 표준** |

```bash
# 현재 설치된 CLI 버전 확인
aws --version
# 출력 예: aws-cli/2.15.0 Python/3.11.6 Darwin/24.x.x botocore/2.15.0
```

---

### 2.3 명령어 → API 변환 과정

CLI는 내부적으로 각 서비스의 **서비스 모델(JSON)** 파일을 읽어 명령어를 API 호출로 변환합니다.

```bash
# CLI v2 서비스 모델 파일 위치 (예: EC2)
ls /usr/local/aws-cli/v2/current/dist/awscli/data/ec2/
# 2016-11-15/  ← API 버전별 디렉토리
#   service-2.json     ← API 스펙 정의 (입력/출력 shape, 에러 목록)
#   paginators-1.json  ← 페이지네이션 토큰 정의
#   waiters-2.json     ← wait 명령 조건 정의
```

**흐름 예시: `aws ec2 describe-instances`**

```
1. awscli가 "ec2" → "describe-instances" 명령어 파싱
2. service-2.json에서 DescribeInstances 스펙 확인
3. 입력 파라미터를 HTTP 쿼리 파라미터로 변환
4. botocore가 자격 증명 로딩 (환경변수 → ~/.aws/credentials → IMDSv2 순)
5. SigV4 서명 생성 후 HTTPS POST/GET 전송
6. 응답 XML/JSON 파싱
7. awscli가 --output 포맷에 맞게 출력
```

---

### 2.4 자격 증명 해석 순서 (botocore 공통)

CLI와 boto3 모두 동일한 botocore 자격 증명 체인을 사용합니다.

```
1. 명시적 파라미터  --profile, --region 등 CLI 인자
2. 환경변수         AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN
3. AWS 설정 파일    ~/.aws/credentials, ~/.aws/config
4. AWS SSO         aws sso login으로 발급된 토큰
5. Container 자격증명  ECS Task Role (AWS_CONTAINER_CREDENTIALS_RELATIVE_URI)
6. EC2 Instance Profile  IMDSv2 (http://169.254.169.254/latest/meta-data/iam/...)
```

> 상세 내용은 `aws-credentials.md` 참고

---

### 2.5 페이지네이션 자동 처리

결과가 많아 여러 페이지로 나뉘는 API는 CLI가 **자동으로 페이지를 넘기며** 전체 결과를 반환합니다.

```bash
# 기본: 자동 페이지네이션 (전체 결과 반환)
aws ec2 describe-instances

# 페이지 1건씩 수동 제어 (--no-paginate로 첫 페이지만)
aws ec2 describe-instances --no-paginate

# 페이지당 항목 수 직접 지정
aws ec2 describe-instances --page-size 50

# paginate 동작 확인 (내부 NextToken 처리 여부)
cat ~/.aws/cli/cache/  # 캐시된 페이지 토큰 확인 가능
```

paginators-1.json 파일에 각 API의 토큰 키 이름이 정의되어 있어 CLI가 자동으로 처리합니다.

```json
// paginators-1.json 예시 (EC2 DescribeInstances)
{
  "DescribeInstances": {
    "input_token": "NextToken",
    "output_token": "NextToken",
    "limit_key": "MaxResults",
    "result_key": "Reservations"
  }
}
```

---

### 2.6 --debug 플래그로 내부 동작 확인

CLI 동작이 예상과 다를 때 `--debug`를 붙이면 SigV4 서명, HTTP 요청/응답 전체를 출력합니다.

```bash
aws s3 ls --debug 2>&1 | head -80
```

출력에서 확인할 수 있는 정보:

```
# 자격 증명 로딩 과정
2024-01-01 00:00:00,000 - MainThread - botocore.credentials - Found credentials in environment variables.

# 실제 HTTP 요청
2024-01-01 00:00:00,100 - MainThread - botocore.endpoint - Making request for ...
  url: https://s3.ap-northeast-2.amazonaws.com/
  method: GET
  headers: {'Authorization': 'AWS4-HMAC-SHA256 Credential=...', ...}

# HTTP 응답
2024-01-01 00:00:00,300 - MainThread - botocore.parsers - Response headers: {'x-amz-request-id': '...'}
```

---

### 2.7 Signature Version 4 (SigV4) 서명 과정

모든 AWS API 요청은 botocore가 SigV4 방식으로 서명합니다.

```
1. Canonical Request 생성
   - HTTP 메서드 + URI + 쿼리스트링 + 헤더 + 페이로드 해시

2. String to Sign 생성
   - "AWS4-HMAC-SHA256" + 날짜 + Credential Scope + Canonical Request 해시

3. Signing Key 파생
   - HMAC(HMAC(HMAC(HMAC("AWS4"+SecretKey, Date), Region), Service), "aws4_request")

4. 최종 서명 생성
   - HMAC(SigningKey, StringToSign) → Authorization 헤더에 포함
```

> SigV4 상세 내용은 `aws-security-sha256-usage.md` 참고

---

### 2.8 실무 적용 코드

**CLI 명령어를 boto3 코드로 그대로 변환하는 방법**

```bash
# CLI 명령어
aws ec2 describe-instances \
  --filters "Name=instance-state-name,Values=running" \
  --query 'Reservations[*].Instances[*].InstanceId' \
  --output text
```

```python
# 동일 동작 boto3 코드 (botocore를 공유하므로 API 호출은 완전히 동일)
import boto3

ec2 = boto3.client('ec2', region_name='ap-northeast-2')
response = ec2.describe_instances(
    Filters=[{'Name': 'instance-state-name', 'Values': ['running']}]
)
instance_ids = [
    i['InstanceId']
    for r in response['Reservations']
    for i in r['Instances']
]
print('\n'.join(instance_ids))
```

**CLI 출력을 스크립트에서 파싱할 때 권장 패턴**

```bash
# text 출력 → awk/while 처리에 적합
aws ec2 describe-instances \
  --query 'Reservations[*].Instances[*].InstanceId' \
  --output text | tr '\t' '\n'

# json 출력 → jq 처리에 적합 (구조 보장)
aws ec2 describe-instances \
  --query 'Reservations[*].Instances[*].InstanceId' \
  --output json | jq -r '.[]'
```

---

### 2.9 보안/비용 Best Practice

- **CLI v2 사용 권장** — v1은 지원 종료 예정. `aws --version`으로 버전 확인 후 v2로 업그레이드
- **자격 증명 파일 직접 작성 지양** — `aws configure sso` 또는 Instance Profile 사용
- **`--dry-run` 활용** — EC2/S3 등 지원 API에서 실제 실행 없이 권한 확인 가능
  ```bash
  aws ec2 stop-instances --instance-ids i-0abc --dry-run
  # 권한 있으면: DryRunOperation 에러 (성공 의미)
  # 권한 없으면: UnauthorizedOperation 에러
  ```
- **`--output json` + jq 조합** — `--output text`는 탭 구분자라 컬럼 수 변화에 취약. 자동화 스크립트에는 json 출력 권장

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**CLI는 성공하는데 boto3는 권한 에러**
- 원인: CLI는 `~/.aws/config`의 `[profile xxx]`를 사용하고, boto3는 기본 프로파일이나 환경변수를 사용해 서로 다른 자격 증명을 참조
- 해결: `AWS_PROFILE` 환경변수로 명시하거나 boto3에 `profile_name` 지정

```python
import boto3
session = boto3.Session(profile_name='my-profile')
ec2 = session.client('ec2')
```

**SSL 인증서 에러 (`SSL: CERTIFICATE_VERIFY_FAILED`)**
- 원인: CLI v2 번들 Python이 시스템 CA 번들과 분리되어 있음. 사내 프록시/커스텀 CA 환경에서 발생
- 해결:
  ```bash
  aws configure set ca_bundle /path/to/custom-ca.pem
  # 또는 환경변수
  export AWS_CA_BUNDLE=/path/to/custom-ca.pem
  ```

**`aws: command not found` (설치 후에도)**
- 원인: PATH에 CLI 설치 경로가 없음
- 해결:
  ```bash
  which aws         # 설치 위치 확인
  echo $PATH        # PATH 확인
  # CLI v2 기본 경로
  export PATH=/usr/local/bin:$PATH
  ```

**페이지네이션 결과가 잘림 (`--no-paginate` 사용 시)**
- 원인: `--no-paginate` 사용 시 첫 페이지만 반환됨
- 해결: `--no-paginate` 제거 후 전체 결과 수신, 또는 `--page-size` 조정

### 3.2 자주 발생하는 문제 (Q&A)

**Q: CLI와 boto3가 같은 API를 호출하는데 결과가 다를 수 있나요?**
- A: API 호출 자체는 동일하지만, CLI는 자동 페이지네이션을 수행하고 boto3는 직접 paginator를 사용하거나 수동으로 NextToken을 처리해야 합니다. 결과 수가 다르다면 페이지네이션 처리 여부를 확인하세요.

**Q: `--query` 문법이 Python이 아닌 것 같은데 무엇인가요?**
- A: JMESPath 표준 쿼리 언어입니다. jmespath.org에서 문법을 확인할 수 있으며, `pip install jmespath`로 Python에서도 동일하게 사용할 수 있습니다.

```python
import jmespath
data = {"Reservations": [{"Instances": [{"InstanceId": "i-0abc"}]}]}
result = jmespath.search("Reservations[*].Instances[*].InstanceId", data)
```

---

## 4. 모니터링 및 알람

CLI는 자체 지표를 CloudWatch에 보내지 않지만, 아래 방법으로 CLI 사용 현황을 추적할 수 있습니다.

**CloudTrail로 CLI 호출 이력 확인**

```bash
# CLI로 발생한 EC2 API 호출 이력 (최근 1시간)
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventSource,AttributeValue=ec2.amazonaws.com \
  --start-time $(date -u -v-1H +%Y-%m-%dT%H:%M:%SZ) \
  --query 'Events[*].[EventTime, EventName, Username]' \
  --output table
```

---

## 5. TIP

- **CLI 자동완성 설정** — 명령어 탭 자동완성 활성화
  ```bash
  # bash
  complete -C aws_completer aws
  # zsh
  autoload bashcompinit && bashcompinit
  complete -C aws_completer aws
  ```
- **`aws configure list`** — 현재 활성화된 설정(프로파일/리전/자격증명 소스) 한눈에 확인
  ```bash
  aws configure list
  #       Name                    Value             Type    Location
  #    profile                <not set>             None    None
  # access_key     ****************XXXX              env
  # secret_key     ****************XXXX              env
  #     region           ap-northeast-2              env    AWS_DEFAULT_REGION
  ```
- **`--cli-auto-prompt`** — CLI v2에서 명령어 자동완성 인터랙티브 모드 활성화 (`aws --cli-auto-prompt`)
- **관련 문서**:
  - `aws-credentials.md` — 자격 증명 우선순위 상세
  - `aws-security-sha256-usage.md` — SigV4 서명 상세
  - `cloudtrail-security-audit.md` — API 호출 감사
