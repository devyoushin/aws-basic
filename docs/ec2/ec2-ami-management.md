# EC2 AMI 관리 & Golden AMI 전략

## 1. 개요

Golden AMI는 베이스 AMI에 공통 패키지, 보안 설정, 에이전트를 미리 설치한 사전 구워진(baked) 이미지다.
인스턴스 시작 시간을 단축하고, 모든 인스턴스가 동일한 기반에서 시작되도록 보장한다.
Packer 또는 EC2 Image Builder로 빌드를 자동화하고, 태그 기반 버전 관리를 통해 신뢰성 있는 배포를 구현한다.

---

## 2. 설명

### 2.1 핵심 개념

**Golden AMI 개념**

```
AWS 공식 Base AMI (Amazon Linux 2023 등)
    ↓ 공통 레이어 추가
Golden AMI:
  - 보안 패치 적용 (OS 업데이트)
  - 공통 에이전트 설치 (SSM Agent, CloudWatch Agent, Datadog 등)
  - 보안 설정 (방화벽, 감사 로그, CIS 기준 강화)
  - 회사 내부 CA 인증서
    ↓ 앱별 커스터마이징
App-specific AMI (선택):
  - 애플리케이션 바이너리 설치
  - 환경별 설정
```

**AMI 버전 관리 태그 전략**

| 태그 | 값 예시 | 설명 |
|------|---------|------|
| `Name` | `golden-al2023-v1.2.3` | AMI 이름 |
| `Version` | `1.2.3` | 의미론적 버전 |
| `BuildDate` | `2024-01-15` | 빌드 날짜 |
| `BaseAMI` | `ami-xxxxxxxx` | 기반 AMI ID |
| `Status` | `approved` / `deprecated` | 사용 승인 상태 |

---

### 2.2 실무 적용 코드

**Packer HCL — Golden AMI 빌드**

```hcl
# golden-ami.pkr.hcl
packer {
  required_plugins {
    amazon = {
      version = ">= 1.3.0"
      source  = "github.com/hashicorp/amazon"
    }
  }
}

variable "aws_region" {
  default = "ap-northeast-2"
}

variable "base_ami_id" {
  description = "Amazon Linux 2023 최신 AMI ID"
  default     = ""  # data source로 동적 조회
}

variable "version" {
  default = "1.0.0"
}

# 최신 AL2023 AMI 동적 조회
data "amazon-ami" "al2023" {
  region = var.aws_region
  filters = {
    name                = "al2023-ami-*-x86_64"
    root-device-type    = "ebs"
    virtualization-type = "hvm"
  }
  most_recent = true
  owners      = ["amazon"]
}

source "amazon-ebs" "golden" {
  region        = var.aws_region
  source_ami    = data.amazon-ami.al2023.id
  instance_type = "t3.medium"   # 빌드용 (실제 운영과 다를 수 있음)
  ssh_username  = "ec2-user"

  ami_name        = "golden-al2023-${var.version}-{{timestamp}}"
  ami_description = "Golden AMI based on AL2023 v${var.version}"

  # 암호화된 AMI 생성
  encrypt_boot = true
  kms_key_id   = "alias/my-ami-key"

  launch_block_device_mappings {
    device_name           = "/dev/xvda"
    volume_size           = 30
    volume_type           = "gp3"
    delete_on_termination = true
    encrypted             = true
  }

  # 빌드 완료 후 태그 추가
  tags = {
    Name       = "golden-al2023-${var.version}"
    Version    = var.version
    BuildDate  = "{{isotime `2006-01-02`}}"
    BaseAMI    = data.amazon-ami.al2023.id
    Status     = "approved"
    ManagedBy  = "packer"
  }

  # 특정 AWS 계정에 AMI 공유
  # ami_users = ["123456789012", "234567890123"]
}

build {
  sources = ["source.amazon-ebs.golden"]

  provisioner "shell" {
    inline = [
      "sudo dnf update -y",
      "sudo dnf install -y amazon-ssm-agent amazon-cloudwatch-agent htop git curl",
      "sudo systemctl enable amazon-ssm-agent",
      # CIS 기준 보안 강화
      "sudo sed -i 's/^PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config",
      "sudo sed -i 's/^#MaxAuthTries.*/MaxAuthTries 4/' /etc/ssh/sshd_config",
      # 감사 로그 활성화
      "sudo systemctl enable --now auditd",
      # 불필요 서비스 비활성화
      "sudo systemctl disable bluetooth 2>/dev/null || true",
      "echo 'Golden AMI 빌드 완료'"
    ]
  }

  # 파일 복사 (회사 CA 인증서 등)
  provisioner "file" {
    source      = "certs/company-ca.crt"
    destination = "/tmp/company-ca.crt"
  }

  provisioner "shell" {
    inline = [
      "sudo cp /tmp/company-ca.crt /etc/pki/ca-trust/source/anchors/",
      "sudo update-ca-trust"
    ]
  }

  # cloud-init 정리 (AMI 배포 전 초기화)
  provisioner "shell" {
    inline = [
      "sudo cloud-init clean --logs",
      "sudo rm -rf /var/lib/cloud/instances/*",
      "sudo rm -f /etc/ssh/ssh_host_*"
    ]
  }
}
```

```bash
# Packer 빌드 실행
packer init .
packer validate golden-ami.pkr.hcl
packer build -var="version=1.2.3" golden-ami.pkr.hcl

# 빌드 결과 AMI ID 확인
aws ec2 describe-images \
  --filters "Name=tag:Version,Values=1.2.3" \
            "Name=tag:Status,Values=approved" \
  --query 'Images[*].{ID:ImageId,Name:Name,Created:CreationDate}' \
  --output table
```

**Terraform — data.aws_ami로 최신 AMI 조회**

```hcl
# 최신 승인된 Golden AMI 조회
data "aws_ami" "golden" {
  most_recent = true
  owners      = ["self"]   # 직접 소유한 AMI

  filter {
    name   = "tag:Status"
    values = ["approved"]
  }

  filter {
    name   = "tag:Name"
    values = ["golden-al2023-*"]
  }

  filter {
    name   = "state"
    values = ["available"]
  }
}

resource "aws_launch_template" "app" {
  image_id = data.aws_ami.golden.id  # 항상 최신 승인 AMI 사용
  ...
}
```

**미사용 AMI 정리 자동화**

```bash
#!/bin/bash
# deprecated 상태의 AMI와 관련 스냅샷 삭제

DEPRECATED_AMIS=$(aws ec2 describe-images \
  --owners self \
  --filters "Name=tag:Status,Values=deprecated" \
  --query 'Images[*].ImageId' \
  --output text)

for AMI_ID in $DEPRECATED_AMIS; do
  echo "AMI 삭제 중: $AMI_ID"

  # 관련 스냅샷 ID 수집
  SNAPSHOT_IDS=$(aws ec2 describe-images \
    --image-ids $AMI_ID \
    --query 'Images[*].BlockDeviceMappings[*].Ebs.SnapshotId' \
    --output text)

  # AMI 등록 해제
  aws ec2 deregister-image --image-id $AMI_ID

  # 관련 스냅샷 삭제
  for SNAPSHOT_ID in $SNAPSHOT_IDS; do
    aws ec2 delete-snapshot --snapshot-id $SNAPSHOT_ID
    echo "  스냅샷 삭제: $SNAPSHOT_ID"
  done
done
```

**EC2 Image Builder 파이프라인 (AWS 네이티브 대안)**

```hcl
resource "aws_imagebuilder_image_pipeline" "golden" {
  name                             = "golden-ami-pipeline"
  image_recipe_arn                 = aws_imagebuilder_image_recipe.golden.arn
  infrastructure_configuration_arn = aws_imagebuilder_infrastructure_configuration.build.arn
  distribution_configuration_arn   = aws_imagebuilder_distribution_configuration.multi_region.arn

  schedule {
    schedule_expression                = "cron(0 0 * * 0)"  # 매주 일요일 자동 빌드
    pipeline_execution_start_condition = "EXPRESSION_MATCH_AND_DEPENDENCY_UPDATES_AVAILABLE"
  }
}
```

---

### 2.3 보안/비용 Best Practice

- **AMI 암호화**: 빌드 시 `encrypt_boot = true` + KMS 키 지정
- **주기적 자동 빌드**: 매주 또는 보안 패치 배포 후 자동 빌드 (EC2 Image Builder 스케줄 또는 CI/CD)
- **AMI 수명 관리**: 90일 이상 된 AMI는 deprecated 처리 → 관련 스냅샷 비용 절감
- **교차 계정 공유**: 멀티 계정 환경에서 AMI를 공유하면 각 계정별 빌드 불필요

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**Packer 빌드 실패 — 네트워크 차단**

```bash
# 오류: "Timeout waiting for SSH"
# 원인: 빌드 인스턴스가 Private 서브넷에 있고 인터넷 접근 불가

# 해결 옵션 1: SSM으로 Packer 연결 (인터넷 불필요)
source "amazon-ebs" "golden" {
  communicator = "ssh"
  ssh_interface = "session_manager"  # SSM 사용
  ...
}

# 해결 옵션 2: VPC 엔드포인트 설정 (S3, Systems Manager 등)
```

**이전 AMI ID가 계속 사용됨**

```bash
# Terraform data source가 캐시를 사용하는 경우
terraform refresh   # 상태 갱신

# 또는 -refresh 강제
terraform plan -refresh=true

# AMI ID 직접 확인
aws ec2 describe-images \
  --owners self \
  --filters "Name=tag:Status,Values=approved" \
  --query 'sort_by(Images, &CreationDate)[-1].ImageId'
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: AMI 암호화 키를 교체하면 기존 AMI는 어떻게 되나요?**
A: 기존 AMI는 이전 KMS 키로 암호화된 상태로 유지됩니다. 새 키로 재암호화하려면 `aws ec2 copy-image --encrypted --kms-key-id new-key`로 복사본 생성 후 원본을 deprecated 처리합니다.

**Q: Packer로 빌드한 AMI를 즉시 다른 리전에 복사하고 싶어요**
A: Packer의 `post-processor "amazon-import"` 또는 Packer manifest + AWS CLI `copy-image`를 빌드 파이프라인에 추가하세요.

---

## 4. 모니터링 및 알람

```hcl
# AMI 빌드 파이프라인 실패 알람 (EC2 Image Builder)
resource "aws_cloudwatch_event_rule" "image_builder_failed" {
  name = "image-builder-pipeline-failed"

  event_pattern = jsonencode({
    source      = ["aws.imagebuilder"]
    detail-type = ["EC2 Image Builder Pipeline Execution State Change"]
    detail = {
      state = { status = ["FAILED"] }
    }
  })
}
```

---

## 5. TIP

- **Semantic Versioning 적용**: `MAJOR.MINOR.PATCH` 버전 체계로 AMI 변경 이력 관리 (MAJOR: OS 변경, MINOR: 패키지 추가, PATCH: 보안 패치)
- **AMI 빌드 CI/CD 연동**: GitHub Actions 또는 Jenkins에서 `packer build`를 트리거하고 AMI ID를 Terraform 변수 또는 SSM Parameter Store에 저장
- **AWS Systems Manager Parameter Store에 AMI ID 저장**: 각 환경(dev/stg/prod)별로 `/infra/ami/golden/latest` 파라미터를 유지하면 Terraform에서 항상 최신 AMI를 참조 가능
