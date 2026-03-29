# EKS 관리형 노드그룹 vs 자체 관리 노드그룹

## 1. 개요

EKS 워커 노드는 크게 두 가지 방식으로 운영할 수 있다.
- **관리형 노드그룹 (Managed Node Group)**: AWS가 AMI, drain, 업그레이드를 자동 관리
- **자체 관리 노드그룹 (Self-Managed Node Group)**: 사용자가 ASG를 직접 관리, 완전한 커스터마이징 가능

대부분의 경우 관리형 노드그룹을 사용하되,
시스템 컴포넌트(모니터링, 로깅 등)는 관리형 노드그룹에,
워크로드는 Karpenter로 관리하는 조합이 현대적인 패턴이다.

---

## 2. 설명

### 2.1 핵심 개념

**관리형 vs 자체 관리 비교**

| 항목 | 관리형 노드그룹 | 자체 관리 |
|------|--------------|----------|
| AMI 관리 | AWS 최신 EKS-optimized AMI 자동 | 사용자 직접 관리 |
| 업그레이드 | 콘솔/CLI로 롤링 업그레이드 | 수동 또는 자동화 직접 구현 |
| drain 처리 | 업그레이드 시 자동 drain | 직접 구현 |
| Spot 지원 | 지원 | 완전한 Mixed Policy 지원 |
| 커스텀 AMI | 제한적 | 완전 자유 |
| Launch Template | 지원 | 지원 |
| Karpenter와 역할 | 시스템 컴포넌트용 | 워크로드는 Karpenter로 대체 |

**Karpenter와의 역할 구분**

```
관리형 노드그룹 (고정 노드):
  - kube-system 컴포넌트 (CoreDNS, VPC CNI, kube-proxy)
  - 모니터링 (Prometheus, Grafana)
  - 로깅 (Fluent Bit)
  - Karpenter 자체
  → 항상 일정 수의 노드 필요, 예측 가능한 워크로드

Karpenter (동적 노드):
  - 애플리케이션 워크로드
  - 배치 작업
  → 수요에 따라 동적으로 노드 추가/제거
```

---

### 2.2 실무 적용 코드

**Terraform — 관리형 노드그룹 생성**

```hcl
resource "aws_eks_node_group" "system" {
  cluster_name    = aws_eks_cluster.main.name
  node_group_name = "system-nodegroup"
  node_role_arn   = aws_iam_role.eks_node.arn

  # 서브넷 (프라이빗 서브넷 권장)
  subnet_ids = var.private_subnet_ids

  # Launch Template 연동 (커스텀 AMI, UserData, 볼륨 설정)
  launch_template {
    id      = aws_launch_template.eks_node.id
    version = aws_launch_template.eks_node.latest_version
  }

  # 인스턴스 타입 (Launch Template에 지정하지 않고 여기서 지정)
  instance_types = ["m5.xlarge"]

  # 스케일링 설정
  scaling_config {
    desired_size = 3
    max_size     = 5
    min_size     = 2
  }

  # 업데이트 설정 (롤링 업데이트)
  update_config {
    max_unavailable = 1   # 한 번에 최대 1개 노드 교체
  }

  # 시스템 컴포넌트 전용 taint
  taint {
    key    = "dedicated"
    value  = "system"
    effect = "NO_SCHEDULE"
  }

  # 레이블
  labels = {
    role        = "system"
    environment = var.environment
  }

  # 라이프사이클 — Terraform 외부에서 desired_size 변경 허용
  lifecycle {
    ignore_changes = [scaling_config[0].desired_size]
  }

  tags = var.tags
}
```

**Launch Template — EKS 노드 전용 설정**

```hcl
resource "aws_launch_template" "eks_node" {
  name_prefix = "eks-node-system-"

  # EKS 관리형 노드그룹은 image_id를 Launch Template에 지정 가능
  # (미지정 시 EKS가 최신 EKS-optimized AMI 자동 선택)
  # image_id = data.aws_ami.eks_node.id

  # IMDSv2 강제
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2   # EKS Pod에서 IMDS 접근 시 hop 2 필요
  }

  # 루트 볼륨 설정
  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_type           = "gp3"
      volume_size           = 100    # EKS 권장 최소 20GB, 컨테이너 이미지 고려 100GB
      iops                  = 3000
      encrypted             = true
      delete_on_termination = true
    }
  }

  # 태그
  tag_specifications {
    resource_type = "instance"
    tags = {
      Name = "eks-node-system"
    }
  }
}
```

**시스템 컴포넌트에 taint toleration 추가**

```yaml
# CoreDNS, VPC CNI 등 시스템 컴포넌트에 toleration 추가
# (시스템 노드그룹에만 스케줄되도록)
apiVersion: apps/v1
kind: Deployment
metadata:
  name: coredns
  namespace: kube-system
spec:
  template:
    spec:
      tolerations:
        - key: dedicated
          value: system
          effect: NoSchedule
      nodeSelector:
        role: system
```

**노드그룹 업그레이드 (AMI 버전 업데이트)**

```bash
# 사용 가능한 릴리스 버전 확인
aws eks describe-addon-versions \
  --kubernetes-version 1.29 \
  --query 'addons[?addonName==`vpc-cni`].addonVersions[0].addonVersion'

# 노드그룹 AMI 업그레이드 (롤링)
aws eks update-nodegroup-version \
  --cluster-name my-cluster \
  --nodegroup-name system-nodegroup \
  --kubernetes-version 1.29

# 진행 상황 모니터링
watch -n 10 "kubectl get nodes -o wide"
```

---

### 2.3 보안/비용 Best Practice

- **시스템 노드그룹은 On-Demand**: 중단 불허 시스템 컴포넌트는 Spot 미사용
- **워크로드 노드는 Karpenter + Spot**: 비용 최적화
- **EKS-optimized AMI 사용**: 커스텀 AMI는 보안 패치 관리 부담 증가
- **taint + label로 시스템 노드와 워크로드 노드 분리**: 서로 영향받지 않도록
- **루트 볼륨 100GB 이상 권장**: 컨테이너 이미지 캐시 고려

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**노드그룹 업그레이드 stuck**

```bash
# 업데이트 상태 확인
aws eks describe-update \
  --name my-cluster \
  --nodegroup-name system-nodegroup \
  --update-id <update-id>
# "ErrorCode": "PodEvictionFailure"

# PDB 확인
kubectl get pdb -A | grep "0 "

# 업데이트 재시도 (PDB 수동 조정 후)
aws eks update-nodegroup-version \
  --cluster-name my-cluster \
  --nodegroup-name system-nodegroup \
  --kubernetes-version 1.29
```

**커스텀 AMI 업데이트 미반영**

```bash
# Launch Template 버전 확인
aws ec2 describe-launch-template-versions \
  --launch-template-id lt-xxxxxxxx \
  --query 'LaunchTemplateVersions[*].{Ver:VersionNumber,ImageId:LaunchTemplateData.ImageId}'

# 노드그룹이 사용하는 Launch Template 버전 확인
aws eks describe-nodegroup \
  --cluster-name my-cluster \
  --nodegroup-name system-nodegroup \
  --query 'nodegroup.launchTemplate'

# 노드그룹 Launch Template 버전 업데이트
aws eks update-nodegroup-version \
  --cluster-name my-cluster \
  --nodegroup-name system-nodegroup \
  --launch-template id=lt-xxxxxxxx,version=3
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: 관리형 노드그룹에서 instance_type을 Launch Template에 지정하면 안 되나요?**
A: Launch Template에 `instance_type`을 지정하면 노드그룹의 `instance_types`와 충돌합니다. Launch Template에는 지정하지 말고 노드그룹 설정에서 `instance_types`로 지정하세요.

**Q: Terraform에서 desired_size가 계속 원래 값으로 돌아옵니다**
A: 클러스터 오토스케일러나 Karpenter가 desired_size를 변경하면 Terraform이 다음 apply 시 원래 값으로 덮어씁니다. `lifecycle { ignore_changes = [scaling_config[0].desired_size] }`를 추가하세요.

---

## 4. 모니터링 및 알람

```hcl
resource "aws_cloudwatch_metric_alarm" "nodegroup_nodes_low" {
  alarm_name          = "eks-nodegroup-nodes-below-min"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 3
  metric_name         = "cluster_node_count"
  namespace           = "ContainerInsights"
  period              = 60
  statistic           = "Minimum"
  threshold           = 2   # 최소 노드 수 미만 시 알람

  dimensions = {
    ClusterName   = var.cluster_name
    NodegroupName = "system-nodegroup"
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
}
```

---

## 5. TIP

- **노드그룹 간 워크로드 격리**: Taint + NodeSelector + PodAffinity 조합으로 특정 워크로드를 특정 노드그룹에 고정
- **노드그룹 수를 최소화**: 너무 많은 노드그룹은 관리 부담 증가 → Karpenter NodePool로 대체하면 노드그룹 수 감소
- **EKS Auto Mode (2024 출시)**: 관리형 노드그룹과 Karpenter 기능을 통합한 완전 관리형 노드 운영 모드 — 신규 클러스터라면 고려할 가치 있음
