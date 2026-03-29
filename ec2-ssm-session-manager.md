# SSM Session Manager — Bastion 없는 EC2 접근

## 1. 개요

SSM Session Manager는 SSH 키와 Bastion 호스트 없이 EC2 인스턴스에 안전하게 접근하는 방법이다.
443 포트 아웃바운드만 있으면 되고, 인바운드 포트를 전혀 열 필요가 없다.
세션 로그는 CloudTrail과 S3에 자동 기록되어 보안 감사에도 유리하다.

---

## 2. 설명

### 2.1 핵심 개념

**동작 원리**

```
사용자 (AWS CLI/콘솔)
    ↓ HTTPS (443)
SSM Service (AWS 관리)
    ↓ SSM Agent (Polling)
EC2 인스턴스 (인바운드 포트 불필요)
```

- EC2 내부의 SSM Agent가 SSM 서비스로 아웃바운드 폴링 (pull 방식)
- 사용자는 SSM 서비스를 통해 터널로 접근
- 인바운드 22번 포트 완전히 차단 가능

**기존 Bastion vs Session Manager 비교**

| 항목 | Bastion Host | Session Manager |
|------|-------------|----------------|
| 인바운드 포트 | 22 (SSH) 허용 필요 | 불필요 |
| SSH 키 관리 | 필요 (분실/유출 위험) | 불필요 |
| 접근 제어 | 보안그룹 + 키 | IAM 정책 |
| 감사 로그 | 별도 설정 필요 | CloudTrail 자동 기록 |
| 비용 | Bastion EC2 운영 비용 | 무료 |
| Private 서브넷 접근 | Bastion 경유 | VPC 엔드포인트 필요 |

**SSM Agent**
- Amazon Linux 2/2023, Ubuntu 20.04 이상에 기본 설치됨
- 미설치 시 수동 설치 가능

---

### 2.2 실무 적용 코드

**IAM Role — EC2에 SSM 권한 부여**

```hcl
resource "aws_iam_role" "ec2_ssm" {
  name = "ec2-ssm-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# SSM 기본 관리 정책 (Session Manager 포함)
resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.ec2_ssm.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "ec2_ssm" {
  name = "ec2-ssm-profile"
  role = aws_iam_role.ec2_ssm.name
}
```

**VPC 엔드포인트 — Private 서브넷 인스턴스용**

```hcl
# Private 서브넷의 EC2는 인터넷 없이 SSM 접근 시 엔드포인트 필요
locals {
  ssm_endpoints = ["ssm", "ssmmessages", "ec2messages"]
}

resource "aws_vpc_endpoint" "ssm" {
  for_each = toset(local.ssm_endpoints)

  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.ap-northeast-2.${each.key}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.ssm_endpoint.id]
  private_dns_enabled = true
}

resource "aws_security_group" "ssm_endpoint" {
  name   = "ssm-endpoint-sg"
  vpc_id = aws_vpc.main.id

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.main.cidr_block]
  }
}
```

**세션 로그 — S3 & CloudWatch Logs 저장 설정**

```hcl
resource "aws_ssm_document" "session_preferences" {
  name            = "SSM-SessionManagerRunShell"
  document_type   = "Session"
  document_format = "JSON"

  content = jsonencode({
    schemaVersion = "1.0"
    description   = "Session Manager 기본 설정"
    sessionType   = "Standard_Stream"
    inputs = {
      s3BucketName        = aws_s3_bucket.ssm_logs.bucket
      s3KeyPrefix         = "session-logs/"
      s3EncryptionEnabled = true
      cloudWatchLogGroupName      = "/aws/ssm/sessions"
      cloudWatchEncryptionEnabled = true
      idleSessionTimeout          = "20"  # 분 단위
    }
  })
}
```

**SSH ProxyCommand — 기존 ssh 명령어 그대로 사용**

```bash
# ~/.ssh/config 설정
Host i-* mi-*
  ProxyCommand sh -c "aws ssm start-session --target %h --document-name AWS-StartSSHSession --parameters portNumber=%p"
  User ec2-user
  IdentityFile ~/.ssh/my-key.pem  # 키 기반 인증은 유지 (선택 사항)

# 이후 기존 ssh 명령어 그대로 사용 가능
ssh i-0123456789abcdef0
```

**포트 포워딩 — RDS/내부 서비스 로컬 접근**

```bash
# RDS (포트 5432)를 로컬 15432로 포워딩
aws ssm start-session \
  --target i-0123456789abcdef0 \
  --document-name AWS-StartPortForwardingSessionToRemoteHost \
  --parameters '{
    "host": ["my-db.xxxx.ap-northeast-2.rds.amazonaws.com"],
    "portNumber": ["5432"],
    "localPortNumber": ["15432"]
  }'

# 이제 로컬에서 접속
psql -h localhost -p 15432 -U admin -d mydb
```

**Run Command — 여러 인스턴스에 명령 일괄 실행**

```bash
# 태그 기준으로 여러 인스턴스에 동시 명령 실행
aws ssm send-command \
  --document-name "AWS-RunShellScript" \
  --targets '[{"Key":"tag:Environment","Values":["production"]}]' \
  --parameters '{"commands":["systemctl status myapp", "df -h"]}' \
  --output-s3-bucket-name my-ssm-output-bucket \
  --output-s3-key-prefix "run-command/"
```

---

### 2.3 보안/비용 Best Practice

- **인바운드 22번 포트 완전 차단**: Session Manager 전환 후 보안그룹에서 SSH 인바운드 즉시 제거
- **IAM 조건으로 접근 제한**: 특정 태그가 붙은 인스턴스만 Session 허용

```json
{
  "Effect": "Allow",
  "Action": "ssm:StartSession",
  "Resource": "arn:aws:ec2:*:*:instance/*",
  "Condition": {
    "StringEquals": {
      "ssm:resourceTag/AllowSSM": "true"
    }
  }
}
```

- **세션 타임아웃 설정**: 유휴 세션 20분 후 자동 종료
- **세션 로그 S3 저장 필수**: 보안 감사 요구사항 충족
- **MFA 조건 추가**: 프로덕션 인스턴스 접근 시 MFA 인증 요구

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**Session Manager 연결 안 됨**

증상: `An error occurred (TargetNotConnected) when calling the StartSession operation`
원인 및 해결:

```bash
# 1. SSM Agent 실행 상태 확인
sudo systemctl status amazon-ssm-agent

# 2. SSM Agent 재시작
sudo systemctl restart amazon-ssm-agent

# 3. SSM 콘솔에서 인스턴스 등록 확인
aws ssm describe-instance-information \
  --filters "Key=InstanceIds,Values=i-xxxxxxxx"

# 원인별 체크리스트
# - IAM Role에 AmazonSSMManagedInstanceCore 정책 부착 여부
# - Private 서브넷 → VPC 엔드포인트 (ssm, ssmmessages, ec2messages) 설정 여부
# - 보안그룹에서 아웃바운드 443 허용 여부
# - SSM Agent 버전 (Amazon Linux 2023은 기본 포함)
```

**Amazon Linux 2023에서 SSM Agent 버전 이슈**

```bash
# SSM Agent 버전 확인
sudo amazon-ssm-agent -version

# 최신 버전으로 업데이트
sudo dnf install -y amazon-ssm-agent
sudo systemctl enable --now amazon-ssm-agent
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: Session Manager 세션은 열리는데 포트 포워딩이 안 됩니다**
A: 포트 포워딩은 Session Manager Plugin이 로컬에 설치되어 있어야 합니다.
```bash
# macOS
brew install session-manager-plugin

# 또는 공식 PKG 설치
curl "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/mac/sessionmanager-bundle.zip" -o bundle.zip
```

**Q: Private 서브넷 인스턴스가 SSM에 등록되지 않습니다**
A: 3개 VPC 엔드포인트가 모두 필요합니다: `ssm`, `ssmmessages`, `ec2messages`. 하나라도 없으면 연결되지 않습니다.

---

## 4. 모니터링 및 알람

```hcl
# 비정상 시간대 Session 시작 알람 (EventBridge)
resource "aws_cloudwatch_event_rule" "ssm_session_start" {
  name = "ssm-session-started"

  event_pattern = jsonencode({
    source      = ["aws.ssm"]
    detail-type = ["AWS API Call via CloudTrail"]
    detail = {
      eventSource = ["ssm.amazonaws.com"]
      eventName   = ["StartSession"]
    }
  })
}
```

**CloudTrail에서 확인할 주요 이벤트**

| 이벤트 | 의미 |
|--------|------|
| `StartSession` | 세션 시작 |
| `TerminateSession` | 세션 종료 |
| `SendCommand` | Run Command 실행 |
| `ResumeSession` | 세션 재연결 |

---

## 5. TIP

- **기존 Bastion 마이그레이션 순서**: 1) IAM Role + SSM 설정 → 2) VPC 엔드포인트 추가 → 3) 접속 테스트 → 4) 보안그룹 22번 포트 제거 → 5) Bastion EC2 종료
- **AWS CLI 세션 시작 단축 스크립트**:
  ```bash
  # ~/.bashrc 또는 ~/.zshrc에 추가
  ssm() {
    aws ssm start-session --target "$1"
  }
  # 사용: ssm i-0123456789abcdef0
  ```
- **AWS 콘솔에서도 접근 가능**: EC2 인스턴스 → "연결" → "Session Manager" 탭
- SSM Session Manager는 **비용 무료** (SSM 자체 무료, VPC 엔드포인트는 시간당 과금)
