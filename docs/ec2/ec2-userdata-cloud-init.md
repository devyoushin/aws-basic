# EC2 UserData & cloud-init 디버깅

## 1. 개요

EC2 UserData는 인스턴스 최초 부팅 시 자동으로 실행되는 스크립트다.
내부적으로 cloud-init이 처리하며, 패키지 설치·서비스 시작·파일 생성 등
인스턴스 초기화 작업을 자동화한다.
실행 실패 시 인스턴스가 부팅은 되지만 설정이 적용되지 않아 조용히 실패하기 쉬우므로
로그 확인 방법을 알아두는 것이 중요하다.

---

## 2. 설명

### 2.1 핵심 개념

**UserData 실행 타이밍**
- 기본적으로 **최초 부팅 1회만 실행** (cloud-init 캐시에 기록)
- Stop → Start 후에는 재실행되지 않음
- 재실행이 필요하면 `cloud-init clean --logs` 후 재부팅

**cloud-init 처리 단계**

| 단계 | 설명 |
|------|------|
| `local` | 네트워크 전 로컬 데이터 처리 |
| `network` | 네트워크 활성화 후 실행 |
| `config` | 모듈 설정 적용 |
| `final` | 마지막 단계, UserData 스크립트 실행 |

**로그 파일 위치**

| 파일 | 내용 |
|------|------|
| `/var/log/cloud-init.log` | cloud-init 상세 디버그 로그 |
| `/var/log/cloud-init-output.log` | UserData 스크립트 stdout/stderr |
| `/var/lib/cloud/instance/user-data.txt` | 적용된 UserData 원본 |
| `/var/lib/cloud/instance/scripts/` | 실행된 스크립트 파일 |

---

### 2.2 실무 적용 코드

**방법 1: Bash 스크립트 방식**

```bash
#!/bin/bash
# 반드시 shebang(#!/bin/bash)으로 시작해야 함
set -euxo pipefail  # 오류 시 즉시 중단, 모든 명령어 출력

# 시스템 업데이트
dnf update -y

# 패키지 설치
dnf install -y \
  amazon-cloudwatch-agent \
  amazon-ssm-agent \
  htop \
  git

# SSM Agent 활성화
systemctl enable --now amazon-ssm-agent

# CloudWatch Agent 설정 파일 생성
cat > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json << 'EOF'
{
  "metrics": {
    "metrics_collected": {
      "mem": { "measurement": ["mem_used_percent"] },
      "disk": { "measurement": ["disk_used_percent"], "resources": ["/"] }
    }
  }
}
EOF

# CloudWatch Agent 시작
/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a fetch-config \
  -m ec2 \
  -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json \
  -s

# 애플리케이션 설치 (S3에서 패키지 다운로드)
aws s3 cp s3://my-artifacts/myapp-1.0.0.rpm /tmp/
dnf install -y /tmp/myapp-1.0.0.rpm
systemctl enable --now myapp

echo "UserData 실행 완료: $(date)" >> /var/log/userdata-complete.log
```

**방법 2: cloud-config YAML 방식**

```yaml
#cloud-config
# 첫 줄은 반드시 #cloud-config 이어야 함

# 패키지 업데이트
package_update: true
package_upgrade: true

# 패키지 설치
packages:
  - htop
  - git
  - amazon-cloudwatch-agent

# 파일 생성
write_files:
  - path: /etc/myapp/config.yaml
    content: |
      server:
        port: 8080
        env: production
    owner: myapp:myapp
    permissions: '0640'

# 서비스 활성화
runcmd:
  - systemctl enable --now amazon-ssm-agent
  - systemctl enable --now myapp

# 부팅 완료 신호 (cfn-signal 또는 직접 로그)
final_message: "cloud-init 완료. 업타임: $UPTIME"
```

**Terraform — UserData 연동**

```hcl
# 방법 1: templatefile로 파라미터 주입
locals {
  userdata = templatefile("${path.module}/userdata.sh.tpl", {
    environment    = var.environment
    s3_bucket      = var.artifact_bucket
    app_version    = var.app_version
  })
}

resource "aws_launch_template" "app" {
  name_prefix   = "app-"
  image_id      = data.aws_ami.al2023.id
  instance_type = "m5.xlarge"

  # base64 인코딩 자동 처리
  user_data = base64encode(local.userdata)

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"  # IMDSv2 강제
    http_put_response_hop_limit = 1
  }
}

# 방법 2: 인라인 heredoc
resource "aws_instance" "bastion" {
  ami           = data.aws_ami.al2023.id
  instance_type = "t3.micro"

  user_data = <<-EOF
    #!/bin/bash
    dnf install -y amazon-ssm-agent
    systemctl enable --now amazon-ssm-agent
  EOF
}
```

**UserData 재실행 방법**

```bash
# cloud-init 캐시 초기화 (재실행 준비)
sudo cloud-init clean --logs

# 또는 특정 단계만 재실행
sudo cloud-init single --name final

# 재부팅하면 UserData 다시 실행됨
sudo reboot
```

**UserData 실행 완료 대기 (다른 서비스에서)**

```bash
# UserData 완료까지 대기 (최대 10분)
cloud-init status --wait --long
echo "cloud-init 완료 상태: $?"
```

---

### 2.3 보안/비용 Best Practice

- **민감 정보 UserData에 절대 포함 금지**: AWS Secrets Manager나 SSM Parameter Store에서 런타임에 조회
  ```bash
  # 올바른 방법: 런타임에 시크릿 조회
  DB_PASSWORD=$(aws secretsmanager get-secret-value \
    --secret-id prod/myapp/db-password \
    --query SecretString --output text | jq -r .password)
  ```
- **`set -euxo pipefail`**: 스크립트 오류 시 즉시 중단, 모든 명령 로깅
- **UserData 크기 제한**: 최대 16KB (초과 시 S3에서 스크립트 다운로드 후 실행)
  ```bash
  #!/bin/bash
  aws s3 cp s3://my-scripts/init.sh /tmp/init.sh
  chmod +x /tmp/init.sh
  /tmp/init.sh
  ```
- **IMDSv2 사용**: UserData 내에서 IMDS 조회 시 토큰 방식 사용

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**UserData 스크립트가 실행됐지만 서비스가 안 됨**

증상: 인스턴스 접속 후 확인하면 설치가 안 되어 있음
원인 및 해결:

```bash
# 1. cloud-init 출력 로그 확인
cat /var/log/cloud-init-output.log

# 2. 오류 발생 위치 확인
grep -i "error\|fail\|exception" /var/log/cloud-init.log

# 흔한 원인:
# - shebang 누락: 첫 줄이 #!/bin/bash가 아님
# - 실행 권한: 스크립트 파일 권한 문제
# - 네트워크: S3/패키지 저장소 접근 불가 (보안그룹, VPC 엔드포인트)
# - set -e 없이 중간 오류 무시됨
```

**UserData 수정 후 미반영**

증상: Launch Template UserData를 수정했는데 새 인스턴스에 반영 안 됨
원인: ASG가 이전 버전 Launch Template을 참조 중
해결:
```bash
# Launch Template 버전 확인
aws ec2 describe-launch-template-versions \
  --launch-template-id lt-xxxxxxxx \
  --query 'LaunchTemplateVersions[*].[VersionNumber,DefaultVersion,CreateTime]'

# ASG Launch Template 버전 업데이트
aws autoscaling update-auto-scaling-group \
  --auto-scaling-group-name my-asg \
  --launch-template LaunchTemplateId=lt-xxxxxxxx,Version='$Latest'
```

**ASG 인스턴스에서 UserData 실행 확인**

```bash
# 현재 인스턴스에 적용된 UserData 확인 (base64 디코딩)
TOKEN=$(curl -sS -X PUT "http://169.254.169.254/latest/api/token" \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")

curl -sS -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/user-data | base64 -d
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: cloud-config YAML을 썼는데 실행이 안 됩니다**
A: 첫 줄이 정확히 `#cloud-config`이어야 합니다 (공백 없이). Bash 스크립트와 혼용 시 `#!/bin/bash`를 첫 줄에 써야 합니다.

**Q: UserData에서 환경변수가 적용되지 않습니다**
A: UserData는 root 권한으로 실행되지만 일반 사용자 환경변수(`.bashrc`, `.profile`)는 로드되지 않습니다. 스크립트 내에서 직접 `export`로 설정하거나 `/etc/environment`에 작성하세요.

**Q: Stop & Start 후 UserData가 다시 실행되지 않습니다**
A: 기본 동작입니다. 매 부팅마다 실행하려면 cloud-init `bootcmd` 모듈을 사용하거나 systemd 서비스로 만드세요.

---

## 4. 모니터링 및 알람

```bash
# UserData 실패 감지를 위한 CloudWatch Logs 에이전트 설정
# /var/log/cloud-init-output.log를 CloudWatch Logs로 전송

cat > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json << 'EOF'
{
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [
          {
            "file_path": "/var/log/cloud-init-output.log",
            "log_group_name": "/ec2/cloud-init-output",
            "log_stream_name": "{instance_id}",
            "timestamp_format": "%Y-%m-%d %H:%M:%S"
          },
          {
            "file_path": "/var/log/cloud-init.log",
            "log_group_name": "/ec2/cloud-init",
            "log_stream_name": "{instance_id}"
          }
        ]
      }
    }
  }
}
EOF
```

```hcl
# cloud-init 실패 키워드 감지 알람
resource "aws_cloudwatch_log_metric_filter" "cloud_init_error" {
  name           = "cloud-init-error"
  pattern        = "\"CRITICAL\" || \"ERROR\" || \"failed\""
  log_group_name = "/ec2/cloud-init-output"

  metric_transformation {
    name      = "CloudInitErrors"
    namespace = "Custom/EC2"
    value     = "1"
  }
}
```

---

## 5. TIP

- **cfn-signal로 CloudFormation에 완료 신호 전송**:
  ```bash
  # UserData 마지막에 추가
  /opt/aws/bin/cfn-signal -e $? \
    --stack ${AWS::StackName} \
    --resource AutoScalingGroup \
    --region ${AWS::Region}
  ```
- **멱등성(Idempotency) 설계**: UserData가 두 번 실행돼도 문제없도록 작성 (`dnf install -y`는 이미 설치되어 있으면 무시)
- **ASG + Launch Template 조합 시**: 인스턴스 교체 전 스테이징에서 UserData 반드시 검증
- **디버깅 편의**: 개발 중에는 `set -x` 추가로 모든 명령어 실행을 로그에 기록
