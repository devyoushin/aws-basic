# EKS VPC CNI & IP 고갈 문제

## 1. 개요

EKS의 기본 CNI (Container Network Interface) 플러그인인 VPC CNI는
Pod에 VPC의 실제 IP를 직접 할당하는 방식이다.
Pod IP = VPC IP이므로 VPC 내 다른 리소스와 직접 통신이 가능하지만,
인스턴스 타입별 ENI/IP 수 제한으로 인해 서브넷 IP 고갈 문제가 발생할 수 있다.

---

## 2. 설명

### 2.1 핵심 개념

**VPC CNI 동작 원리**

```
노드 시작 시:
    EC2 인스턴스에 ENI (Elastic Network Interface) 부착
    각 ENI에 Secondary IP 할당 (VPC 서브넷 IP 소비)

Pod 생성 시:
    CNI가 미리 할당된 Secondary IP 중 하나를 Pod에 배정
    Pod IP = VPC IP (직접 라우팅 가능)
```

**인스턴스 타입별 ENI/IP 제한**

| 인스턴스 타입 | 최대 ENI | ENI당 최대 IP | 최대 Pod 수 (계산값) |
|-------------|---------|-------------|---------------------|
| t3.medium | 3 | 6 | (3×6) - 3 = 15 |
| m5.large | 3 | 10 | (3×10) - 3 = 27 |
| m5.xlarge | 4 | 15 | (4×15) - 4 = 56 |
| m5.4xlarge | 8 | 30 | (8×30) - 8 = 232 |
| c5.18xlarge | 15 | 50 | (15×50) - 15 = 735 |

계산 공식: `(최대 ENI × ENI당 최대 IP) - 최대 ENI` (각 ENI의 Primary IP는 Pod에 할당 불가)

실제 최대 Pod 수는 `aws-node` DaemonSet의 `MAX_POD` 환경변수로 제한되며,
EKS는 별도로 인스턴스 타입별 최대 Pod 수를 설정한다.

**IP Prefix Delegation — Pod 밀도 대폭 향상**

기존: ENI의 Secondary IP를 1개씩 할당
Prefix 방식: ENI에 `/28` CIDR 블록(16개 IP)을 할당 → Pod 밀도 약 4배 향상

| 인스턴스 타입 | 기존 최대 Pod | Prefix Delegation 최대 Pod |
|-------------|-------------|--------------------------|
| m5.xlarge | 56 | 234 |
| m5.4xlarge | 232 | 858 |

---

### 2.2 실무 적용 코드

**IP Prefix Delegation 활성화**

```bash
# aws-node DaemonSet에 환경변수 설정
kubectl set env daemonset aws-node \
  -n kube-system \
  ENABLE_PREFIX_DELEGATION=true

# Warm Prefix 설정 (사전 할당할 /28 블록 수)
kubectl set env daemonset aws-node \
  -n kube-system \
  WARM_PREFIX_TARGET=1      # 항상 여유 /28 블록 1개 유지
  # MINIMUM_IP_TARGET=10    # 최소 10개 IP 유지
  # WARM_IP_TARGET=5        # 항상 여유 5개 IP 유지

# 설정 확인
kubectl get daemonset aws-node -n kube-system -o yaml | grep -A 30 env
```

**Terraform — EKS 클러스터 생성 시 VPC CNI 설정**

```hcl
resource "aws_eks_addon" "vpc_cni" {
  cluster_name = aws_eks_cluster.main.name
  addon_name   = "vpc-cni"

  configuration_values = jsonencode({
    env = {
      ENABLE_PREFIX_DELEGATION = "true"
      WARM_PREFIX_TARGET       = "1"
    }
  })

  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"
}
```

**노드별 IP 사용 현황 확인**

```bash
# 노드별 할당된 IP와 사용 중인 IP 확인
kubectl get nodes -o custom-columns=\
'NAME:.metadata.name,\
ALLOCATABLE_PODS:.status.allocatable.pods,\
CAPACITY_PODS:.status.capacity.pods'

# aws-node 로그로 IP 할당 상태 확인
kubectl logs -n kube-system -l k8s-app=aws-node --tail=50

# ENI 및 IP 상세 정보
kubectl describe node <node-name> | grep -A 20 "Allocated resources"
```

**서브넷 IP 사용률 확인 (AWS CLI)**

```bash
# 서브넷별 사용 가능한 IP 수 확인
aws ec2 describe-subnets \
  --subnet-ids subnet-xxxxxxxx \
  --query 'Subnets[*].[SubnetId,AvailableIpAddressCount,CidrBlock]' \
  --output table

# EKS 노드 서브넷의 IP 소비 추이 모니터링 (CloudWatch)
aws cloudwatch get-metric-statistics \
  --namespace AWS/EC2 \
  --metric-name NetworkInterfacesUsed \
  --dimensions Name=InstanceId,Value=i-xxxxxxxx \
  --start-time $(date -u -v-1d +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 3600 \
  --statistics Average
```

**Custom Networking — 노드와 Pod를 다른 서브넷에 배치**

```bash
# Pod를 별도 서브넷(더 큰 CIDR)에서 IP 할당
kubectl set env daemonset aws-node \
  -n kube-system \
  AWS_VPC_K8S_CNI_CUSTOM_NETWORK_CFG=true \
  ENI_CONFIG_LABEL_DEF=topology.kubernetes.io/zone

# ENIConfig 리소스 생성 (AZ별 Pod 전용 서브넷 지정)
cat <<EOF | kubectl apply -f -
apiVersion: crd.k8s.amazonaws.com/v1alpha1
kind: ENIConfig
metadata:
  name: ap-northeast-2a
spec:
  subnet: subnet-pod-subnet-2a   # Pod 전용 서브넷
  securityGroups:
    - sg-xxxxxxxxxxxxxxxxx
EOF
```

---

### 2.3 보안/비용 Best Practice

- **서브넷 설계 단계에서 여유 있게 CIDR 할당**: 노드용 `/24`(256개) + Pod용 `/19`(8192개) 분리 권장
- **Prefix Delegation 활성화**: 대규모 클러스터에서 서브넷 IP 고갈 방지의 핵심
- **노드 서브넷과 Pod 서브넷 분리 (Custom Networking)**: 노드와 Pod가 같은 서브넷을 공유하면 IP 계산이 복잡해짐
- **Security Groups for Pods**: Pod 레벨 보안그룹 적용 가능 (Nitro 인스턴스 필요)

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**"Too many pods" — 노드 Pod 한계 초과**

```bash
# Pod가 Pending 상태
kubectl describe pod <pod> | grep -A 5 Events
# "Too many pods" 또는 "Insufficient pods"

# 노드별 현재 Pod 수 확인
kubectl get pods -A -o wide | awk '{print $8}' | sort | uniq -c | sort -rn

# 인스턴스 타입 최대 Pod 수 확인
aws ec2 describe-instance-types \
  --instance-types m5.xlarge \
  --query 'InstanceTypes[*].NetworkInfo'

# 해결: 더 큰 인스턴스 타입 사용 또는 Prefix Delegation 활성화
```

**서브넷 IP 고갈**

```bash
# 서브넷 가용 IP 수 확인
aws ec2 describe-subnets \
  --query 'Subnets[?AvailableIpAddressCount<`20`].[SubnetId,AvailableIpAddressCount]'

# 즉각 대응: 새 서브넷 추가 후 노드그룹에 연결
# 또는 Prefix Delegation으로 IP 효율 향상

# 장기 대응: VPC CIDR 추가 (Secondary CIDR block)
aws ec2 associate-vpc-cidr-block \
  --vpc-id vpc-xxxxxxxx \
  --cidr-block 100.64.0.0/16
```

**ENI 할당 실패**

```bash
# aws-node 로그에서 ENI 관련 오류 확인
kubectl logs -n kube-system -l k8s-app=aws-node | grep -i "error\|failed\|ENI"

# EC2 인스턴스의 ENI 한계 도달 여부 확인
aws ec2 describe-network-interfaces \
  --filters Name=attachment.instance-id,Values=i-xxxxxxxx \
  --query 'NetworkInterfaces[*].NetworkInterfaceId'
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: Prefix Delegation 활성화 후 기존 노드에 바로 적용되나요?**
A: 아닙니다. Prefix Delegation은 새로 시작되는 노드에만 적용됩니다. 기존 노드는 교체가 필요합니다. ASG의 Instance Refresh를 사용하세요.

**Q: Pod IP가 VPC 외부에서 라우팅 가능한가요?**
A: VPC 내에서는 가능합니다. VPC 외부(온프레미스, 다른 VPC)에서는 VPC Peering 또는 Transit Gateway가 필요하며, Pod 서브넷 CIDR을 라우팅 테이블에 추가해야 합니다.

---

## 4. 모니터링 및 알람

```hcl
# 서브넷 가용 IP 수 알람 (CloudWatch)
resource "aws_cloudwatch_metric_alarm" "subnet_ip_low" {
  alarm_name          = "subnet-available-ips-low"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  metric_name         = "SubnetAvailableIpCount"
  namespace           = "ContainerInsights"
  period              = 300
  statistic           = "Minimum"
  threshold           = 50   # 가용 IP 50개 미만 시 알람

  dimensions = {
    ClusterName = var.cluster_name
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
}
```

**CloudWatch Container Insights 지표**

| 지표 | 의미 |
|------|------|
| `SubnetAvailableIpCount` | 서브넷 가용 IP 수 |
| `NetworkInterfacesUsed` | 사용 중인 ENI 수 |
| `pod_number_of_running_pods` | 노드별 실행 중인 Pod 수 |

---

## 5. TIP

- **VPC 설계 시 Pod IP 수요 사전 계산**: `노드 수 × 노드당 최대 Pod 수`가 서브넷 CIDR 범위 안에 들어야 함
- **`/19` 서브넷 = 8,190개 IP**: 중규모 클러스터(노드 100대, 노드당 최대 80 Pod)에서 여유 있게 사용 가능
- **100.64.0.0/10 (CGNAT)**: AWS VPC에 Secondary CIDR로 추가 가능한 대규모 IP 범위 — Pod 전용으로 활용 시 서브넷 IP 고갈 문제를 근본적으로 해결
