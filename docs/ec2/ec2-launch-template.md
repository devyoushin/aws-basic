# EC2 Launch Template 관리

## 1. 개요

Launch Template은 EC2 인스턴스 시작에 필요한 설정을 버전 관리되는 템플릿으로 저장하는 기능이다.
ASG, EKS 관리형 노드그룹, Karpenter 등 모든 EC2 자동화 도구의 기반이 된다.
기존 Launch Configuration은 현재 신규 생성이 불가하므로 Launch Template 사용이 필수다.

---

## 2. 설명

### 2.1 핵심 개념

**Launch Template vs Launch Configuration 비교**

| 항목 | Launch Template | Launch Configuration |
|------|----------------|---------------------|
| 버전 관리 | 지원 (여러 버전) | 미지원 (변경 불가) |
| Spot 인스턴스 | 지원 | 제한적 |
| 네트워크 인터페이스 | 여러 개 지정 | 1개만 |
| 신규 생성 | 가능 | **불가 (deprecated)** |
| 파라미터 상속 | 부분 오버라이드 지원 | 미지원 |

**버전 관리 개념**
- 매 수정마다 새 버전 생성 (버전 번호 자동 증가)
- `$Default`: 명시적으로 지정한 기본 버전
- `$Latest`: 항상 최신 버전
- ASG 등에서 `$Latest` 참조 시 자동으로 최신 버전 사용

---

### 2.2 실무 적용 코드

**Terraform — Launch Template 전체 설정**

```hcl
data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }

  filter {
    name   = "state"
    values = ["available"]
  }
}

resource "aws_launch_template" "app" {
  name_prefix   = "app-"
  image_id      = data.aws_ami.al2023.id
  instance_type = "m5.xlarge"

  # IAM 인스턴스 프로파일
  iam_instance_profile {
    name = aws_iam_instance_profile.app.name
  }

  # 네트워크 설정
  network_interfaces {
    associate_public_ip_address = false
    security_groups             = [aws_security_group.app.id]
    delete_on_termination       = true
  }

  # EBS 루트 볼륨
  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_type           = "gp3"
      volume_size           = 50
      iops                  = 3000
      throughput            = 125
      encrypted             = true
      kms_key_id            = aws_kms_key.ebs.arn
      delete_on_termination = true
    }
  }

  # 추가 데이터 볼륨
  block_device_mappings {
    device_name = "/dev/sdb"
    ebs {
      volume_type           = "gp3"
      volume_size           = 100
      iops                  = 3000
      encrypted             = true
      delete_on_termination = true
    }
  }

  # IMDSv2 강제 설정 (보안 필수)
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  # UserData
  user_data = base64encode(templatefile("${path.module}/userdata.sh.tpl", {
    environment = var.environment
  }))

  # 모니터링 (상세 모니터링 1분 간격)
  monitoring {
    enabled = true
  }

  # 태그
  tag_specifications {
    resource_type = "instance"
    tags = {
      Name        = "app-${var.environment}"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }

  tag_specifications {
    resource_type = "volume"
    tags = {
      Name        = "app-${var.environment}-vol"
      Environment = var.environment
    }
  }

  lifecycle {
    create_before_destroy = true
  }
}
```

**ASG와 Launch Template 연동**

```hcl
resource "aws_autoscaling_group" "app" {
  name                = "app-asg-${var.environment}"
  min_size            = 2
  max_size            = 10
  desired_capacity    = 2
  vpc_zone_identifier = var.private_subnet_ids

  launch_template {
    id      = aws_launch_template.app.id
    version = "$Latest"  # 항상 최신 버전 사용
    # version = aws_launch_template.app.latest_version  # 특정 버전 고정
  }

  # 인스턴스 갱신 설정 (새 버전 배포 시 롤링 교체)
  instance_refresh {
    strategy = "Rolling"
    preferences {
      min_healthy_percentage = 50
      instance_warmup        = 300
    }
  }
}
```

**EKS 관리형 노드그룹 + Launch Template 연동**

```hcl
resource "aws_eks_node_group" "app" {
  cluster_name    = aws_eks_cluster.main.name
  node_group_name = "app-nodegroup"
  node_role_arn   = aws_iam_role.eks_node.arn
  subnet_ids      = var.private_subnet_ids

  launch_template {
    id      = aws_launch_template.eks_node.id
    version = aws_launch_template.eks_node.latest_version
  }

  scaling_config {
    desired_size = 3
    max_size     = 10
    min_size     = 1
  }

  # Launch Template 변경 시 노드 롤링 업데이트
  update_config {
    max_unavailable = 1
  }
}

# EKS 노드 전용 Launch Template (UserData 형식 주의)
resource "aws_launch_template" "eks_node" {
  name_prefix   = "eks-node-"
  image_id      = data.aws_ami.eks_node.id
  # EKS 노드그룹에서 instance_type은 launch template이 아닌 node_group에서 지정

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2  # EKS는 Pod에서 IMDS 접근 시 hop 2 필요
  }

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_type = "gp3"
      volume_size = 100
      encrypted   = true
    }
  }

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name = "eks-node-${var.environment}"
    }
  }
}
```

**AWS CLI — 버전 관리**

```bash
# 현재 버전 목록 확인
aws ec2 describe-launch-template-versions \
  --launch-template-id lt-xxxxxxxx \
  --query 'LaunchTemplateVersions[*].{Ver:VersionNumber,Default:DefaultVersion,Created:CreateTime}' \
  --output table

# 특정 버전을 Default로 설정
aws ec2 modify-launch-template \
  --launch-template-id lt-xxxxxxxx \
  --default-version 3

# 이전 버전 삭제 (현재 사용 중인 버전은 삭제 불가)
aws ec2 delete-launch-template-versions \
  --launch-template-id lt-xxxxxxxx \
  --versions 1 2
```

---

### 2.3 보안/비용 Best Practice

- **IMDSv2 강제 필수**: `http_tokens = "required"` 항상 설정
- **EBS 암호화 기본화**: 모든 볼륨에 `encrypted = true` + KMS 키 지정
- **버전 고정 vs $Latest**: 프로덕션 ASG는 특정 버전으로 고정, 개발 환경은 `$Latest` 허용
- **Launch Template 1개에 공통 설정**: 환경별 차이는 ASG 오버라이드로 처리

```hcl
# ASG에서 Launch Template 일부 오버라이드
mixed_instances_policy {
  launch_template {
    launch_template_specification {
      launch_template_id = aws_launch_template.app.id
      version            = "$Default"
    }
    # 인스턴스 타입만 오버라이드
    override {
      instance_type = "m5.xlarge"
    }
    override {
      instance_type = "m5a.xlarge"
    }
  }
}
```

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**새 버전 배포 후 ASG 인스턴스가 교체되지 않음**

증상: Launch Template을 수정했는데 기존 인스턴스가 그대로 운영 중
원인: ASG는 Launch Template 변경 시 자동으로 기존 인스턴스를 교체하지 않음
해결:
```bash
# Instance Refresh로 롤링 교체 시작
aws autoscaling start-instance-refresh \
  --auto-scaling-group-name my-asg \
  --preferences '{"MinHealthyPercentage": 50, "InstanceWarmup": 300}'

# 진행 상태 확인
aws autoscaling describe-instance-refreshes \
  --auto-scaling-group-name my-asg
```

**Launch Template 삭제 불가**

증상: `DependencyViolation: Launch Template is in use`
원인: ASG/EKS 노드그룹이 해당 템플릿을 참조 중
해결:
```bash
# 참조 중인 ASG 확인
aws autoscaling describe-auto-scaling-groups \
  --query 'AutoScalingGroups[?LaunchTemplate.LaunchTemplateId==`lt-xxxxxxxx`].AutoScalingGroupName'

# ASG 삭제 또는 Launch Template 교체 후 삭제
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: EKS 노드그룹에서 Launch Template의 instance_type을 지정하면 안 된다고 합니다**
A: 맞습니다. EKS 관리형 노드그룹은 instance_type을 `aws_eks_node_group`의 `instance_types`에서 지정합니다. Launch Template에 instance_type이 있으면 충돌이 발생합니다.

**Q: Launch Template 변경 후 Karpenter가 새 노드를 이전 설정으로 시작합니다**
A: Karpenter의 `EC2NodeClass`가 Launch Template을 직접 참조하지 않는 경우, Karpenter 자체 설정을 업데이트해야 합니다. Karpenter는 Launch Template을 생성/관리하므로 `EC2NodeClass`의 `userData` 또는 `amiSelector`를 수정하세요.

---

## 4. 모니터링 및 알람

```hcl
# Launch Template 버전 변경 추적 (CloudTrail)
resource "aws_cloudwatch_event_rule" "lt_modified" {
  name = "launch-template-modified"

  event_pattern = jsonencode({
    source      = ["aws.ec2"]
    detail-type = ["AWS API Call via CloudTrail"]
    detail = {
      eventSource = ["ec2.amazonaws.com"]
      eventName   = ["CreateLaunchTemplateVersion", "ModifyLaunchTemplate"]
    }
  })
}
```

**확인할 CloudTrail 이벤트**

| 이벤트 | 의미 |
|--------|------|
| `CreateLaunchTemplate` | 새 템플릿 생성 |
| `CreateLaunchTemplateVersion` | 새 버전 추가 |
| `ModifyLaunchTemplate` | Default 버전 변경 |
| `DeleteLaunchTemplate` | 템플릿 삭제 |

---

## 5. TIP

- **Launch Template 변경 내역 확인**: 각 버전의 변경 사항을 설명에 기록해 두면 롤백 시 유용
  ```bash
  aws ec2 describe-launch-template-versions \
    --launch-template-id lt-xxxxxxxx \
    --query 'LaunchTemplateVersions[*].{Ver:VersionNumber,Desc:VersionDescription}'
  ```
- **Terraform `create_before_destroy`**: Launch Template 교체 시 다운타임 방지
- **AMI 자동 갱신**: `data.aws_ami`의 `most_recent = true`로 항상 최신 AMI 참조 (단, 예상치 못한 AMI 교체 방지를 위해 프로덕션에서는 AMI ID를 고정하는 것도 고려)
- EKS 노드그룹에서 `http_put_response_hop_limit = 2` 설정 필수 — Pod 내부에서 IMDS에 접근할 때 hop이 2가 필요함
