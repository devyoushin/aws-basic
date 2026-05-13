# EKS VPC CNI Custom Networking — Secondary 서브넷으로 Pod IP 분리

## 1. 개요

VPC CNI 기본 동작에서 Pod는 노드와 동일한 서브넷에서 IP를 할당받는다.
**Custom Networking**은 이 동작을 변경해 Pod IP를 **별도의 서브넷(Secondary 서브넷)** 에서 할당받도록 하는 기능이다.

**주요 사용 목적**

| 목적 | 설명 |
|------|------|
| IP 고갈 해소 | 노드 서브넷(/24, 256개)이 부족할 때 Pod 전용으로 큰 서브넷 (/19, /16) 분리 |
| Secondary CIDR 활용 | VPC에 100.64.0.0/10 같은 추가 CIDR을 붙이고 Pod 전용으로 사용 |
| 노드/Pod 트래픽 분리 | ENI 분리로 노드 관리 트래픽과 Pod 데이터 트래픽 서브넷 격리 |
| Security Group 분리 | Pod ENI에 별도 SG 적용 (Security Groups for Pods와 병행 가능) |

---

## 2. 동작 원리

### 2.1 기본 모드 vs Custom Networking 비교

```
[기본 모드]
노드(eth0) ─── 노드 서브넷(10.0.1.0/24)
               ├── 노드 Primary IP: 10.0.1.10
               ├── Secondary IP → Pod A: 10.0.1.20    ← 같은 서브넷에서 Pod IP 할당
               └── Secondary IP → Pod B: 10.0.1.21

[Custom Networking]
노드(eth0) ─── 노드 서브넷(10.0.1.0/24)
               └── 노드 Primary IP: 10.0.1.10  ← eth0 IP는 Pod에 사용되지 않음(낭비)

노드(eth1) ─── Pod 서브넷(100.64.1.0/19)       ← ENIConfig로 지정한 별도 서브넷
               ├── Secondary IP → Pod A: 100.64.1.1
               ├── Secondary IP → Pod B: 100.64.1.2
               └── Secondary IP → Pod C: 100.64.1.3
```

### 2.2 ENIConfig CRD — 핵심 매개체

aws-node는 노드에 Secondary ENI를 붙일 때 **ENIConfig** CRD를 참조한다.
ENIConfig에는 "어느 서브넷에서 IP를 가져올지"와 "어느 Security Group을 붙일지"가 정의된다.

```
노드 기동
  └─▶ aws-node가 노드의 라벨 값을 읽음 (ENI_CONFIG_LABEL_DEF로 지정한 라벨 키)
        └─▶ 해당 값과 이름이 같은 ENIConfig 조회
              └─▶ ENIConfig의 subnet + securityGroups로 Secondary ENI 생성
                    └─▶ Secondary ENI에 할당된 IP → Pod에 배정
```

### 2.3 Primary ENI IP 낭비 문제

Custom Networking을 활성화하면 **eth0(Primary ENI)의 Secondary IP 슬롯이 Pod에 사용되지 않는다.**
Primary ENI 자체는 노드 통신에 사용되지만 IP 슬롯은 버려진다.

예) m5.xlarge (ENI당 IP 15개):
- 기본 모드: (4 ENI × 15 IP) - 4 = **56 Pod**
- Custom Networking (Prefix Delegation 없음): (3 ENI × 15 IP) - 3 = **42 Pod** ← 감소
- Custom Networking + Prefix Delegation: (3 ENI × 16 prefix × 16 IP) = **~750 Pod** ← 대폭 증가

**따라서 Custom Networking은 Prefix Delegation과 함께 사용하는 것이 표준 패턴이다.**

---

## 3. 전제 조건 — VPC Secondary CIDR 설계

### 3.1 Secondary CIDR 선택

Pod 전용으로 별도 CIDR을 VPC에 추가할 때 주로 **100.64.0.0/10** (RFC 6598 공유 주소 공간)을 사용한다.

| CIDR | IP 수 | 장점 |
|------|-------|------|
| `100.64.0.0/10` | 약 4백만 | 온프레미스/인터넷 라우팅 없음, AWS VPC Secondary CIDR 지원, 충돌 가능성 최소 |
| `10.x.0.0/16` | 65,536 | 기존 10.x 대역 연장, 온프레미스와 겹칠 위험 있음 |
| `172.16.x.0/19` | 8,190 | 소규모 클러스터 |

> 온프레미스 네트워크가 10.x, 172.16.x 대역을 사용 중이면 100.64.0.0/10이 안전하다.

### 3.2 Secondary CIDR 추가 및 Pod 전용 서브넷 생성

```bash
# VPC에 Secondary CIDR 추가
aws ec2 associate-vpc-cidr-block \
  --vpc-id vpc-0abc123def456 \
  --cidr-block 100.64.0.0/16

# 결과 확인
aws ec2 describe-vpcs \
  --vpc-ids vpc-0abc123def456 \
  --query 'Vpcs[0].CidrBlockAssociationSet[*].{CIDR:CidrBlock,State:CidrBlockState.State}'
```

```bash
# AZ별 Pod 전용 서브넷 생성 (/19 = 8,190 IPs per AZ)
aws ec2 create-subnet \
  --vpc-id vpc-0abc123def456 \
  --cidr-block 100.64.0.0/19 \
  --availability-zone ap-northeast-2a \
  --tag-specifications 'ResourceType=subnet,Tags=[{Key=Name,Value=eks-pod-subnet-2a},{Key=Purpose,Value=eks-pod}]'

aws ec2 create-subnet \
  --vpc-id vpc-0abc123def456 \
  --cidr-block 100.64.32.0/19 \
  --availability-zone ap-northeast-2b \
  --tag-specifications 'ResourceType=subnet,Tags=[{Key=Name,Value=eks-pod-subnet-2b},{Key=Purpose,Value=eks-pod}]'

aws ec2 create-subnet \
  --vpc-id vpc-0abc123def456 \
  --cidr-block 100.64.64.0/19 \
  --availability-zone ap-northeast-2c \
  --tag-specifications 'ResourceType=subnet,Tags=[{Key=Name,Value=eks-pod-subnet-2c},{Key=Purpose,Value=eks-pod}]'
```

```bash
# Pod 서브넷에 기존 라우팅 테이블 연결 (노드 서브넷과 동일한 라우팅 테이블 사용 가능)
aws ec2 associate-route-table \
  --route-table-id rtb-0abc123 \
  --subnet-id subnet-pod-2a

# Pod 서브넷에 EKS CNI 요구 태그 추가
aws ec2 create-tags \
  --resources subnet-pod-2a subnet-pod-2b subnet-pod-2c \
  --tags Key=kubernetes.io/cluster/my-cluster,Value=shared
```

---

## 4. Custom Networking 설정

### 4.1 aws-node 환경변수 설정

```bash
# Custom Networking 활성화
kubectl set env daemonset aws-node \
  -n kube-system \
  AWS_VPC_K8S_CNI_CUSTOM_NETWORK_CFG=true

# 노드의 어떤 라벨 값으로 ENIConfig 이름을 매핑할지 지정
# topology.kubernetes.io/zone = AZ 이름 (ap-northeast-2a) → ENIConfig 이름과 일치
kubectl set env daemonset aws-node \
  -n kube-system \
  ENI_CONFIG_LABEL_DEF=topology.kubernetes.io/zone

# Prefix Delegation 함께 활성화 (Primary ENI 낭비 보상)
kubectl set env daemonset aws-node \
  -n kube-system \
  ENABLE_PREFIX_DELEGATION=true \
  WARM_PREFIX_TARGET=1

# 설정 확인
kubectl get daemonset aws-node -n kube-system \
  -o jsonpath='{.spec.template.spec.containers[0].env[*]}' | \
  python3 -m json.tool | grep -E "AWS_VPC|ENI_CONFIG|PREFIX"
```

### 4.2 ENIConfig CRD 생성 (AZ별)

ENIConfig 이름은 반드시 `ENI_CONFIG_LABEL_DEF`로 지정한 **라벨의 값**과 일치해야 한다.
`topology.kubernetes.io/zone` 사용 시 → 이름 = AZ 이름

```yaml
# eni-config-2a.yaml
apiVersion: crd.k8s.amazonaws.com/v1alpha1
kind: ENIConfig
metadata:
  name: ap-northeast-2a           # 반드시 AZ 이름과 일치
spec:
  subnet: subnet-pod-2a-xxxxxxxx  # Pod 전용 서브넷 ID (AZ-2a)
  securityGroups:
    - sg-eks-pod-xxxxxxxxxx       # Pod ENI에 적용할 Security Group
```

```yaml
# eni-config-2b.yaml
apiVersion: crd.k8s.amazonaws.com/v1alpha1
kind: ENIConfig
metadata:
  name: ap-northeast-2b
spec:
  subnet: subnet-pod-2b-xxxxxxxx
  securityGroups:
    - sg-eks-pod-xxxxxxxxxx
```

```yaml
# eni-config-2c.yaml
apiVersion: crd.k8s.amazonaws.com/v1alpha1
kind: ENIConfig
metadata:
  name: ap-northeast-2c
spec:
  subnet: subnet-pod-2c-xxxxxxxx
  securityGroups:
    - sg-eks-pod-xxxxxxxxxx
```

```bash
kubectl apply -f eni-config-2a.yaml
kubectl apply -f eni-config-2b.yaml
kubectl apply -f eni-config-2c.yaml

# 생성 확인
kubectl get eniconfigs
# NAME                AGE
# ap-northeast-2a     10s
# ap-northeast-2b     10s
# ap-northeast-2c     10s
```

### 4.3 노드에 라벨 확인

`topology.kubernetes.io/zone` 라벨은 EKS가 노드 기동 시 자동으로 붙인다.
별도 작업 없이 노드 라벨만 확인하면 된다:

```bash
kubectl get nodes --show-labels | grep topology.kubernetes.io/zone
# 또는
kubectl get nodes -o custom-columns=\
  'NAME:.metadata.name,ZONE:.metadata.labels.topology\.kubernetes\.io/zone'
```

### 4.4 노드 교체 (필수)

Custom Networking 설정은 **노드가 새로 시작될 때** aws-node가 ENIConfig를 참조해 ENI를 붙이므로,
**기존 실행 중인 노드는 반드시 교체해야 한다.**

```bash
# Managed Node Group — Instance Refresh
aws eks update-nodegroup-config \
  --cluster-name my-cluster \
  --nodegroup-name my-nodegroup

# 또는 노드를 하나씩 drain & terminate
kubectl drain <node-name> --ignore-daemonsets --delete-emptydir-data
aws ec2 terminate-instances --instance-ids i-xxxxxxxx

# Karpenter 사용 중이면 노드 어노테이션으로 교체
kubectl annotate node <node-name> karpenter.sh/do-not-disrupt-
kubectl delete node <node-name>
```

---

## 5. Terraform 전체 코드

```hcl
# ─────────────────────────────────────────
# VPC Secondary CIDR
# ─────────────────────────────────────────
resource "aws_vpc_ipv4_cidr_block_association" "pod_cidr" {
  vpc_id     = aws_vpc.main.id
  cidr_block = "100.64.0.0/16"
}

# ─────────────────────────────────────────
# Pod 전용 서브넷 (AZ별)
# ─────────────────────────────────────────
locals {
  pod_subnets = {
    "ap-northeast-2a" = { cidr = "100.64.0.0/19",  name = "eks-pod-2a" }
    "ap-northeast-2b" = { cidr = "100.64.32.0/19", name = "eks-pod-2b" }
    "ap-northeast-2c" = { cidr = "100.64.64.0/19", name = "eks-pod-2c" }
  }
}

resource "aws_subnet" "pod" {
  for_each = local.pod_subnets

  vpc_id            = aws_vpc.main.id
  cidr_block        = each.value.cidr
  availability_zone = each.key

  tags = {
    Name                                        = each.value.name
    "kubernetes.io/cluster/${var.cluster_name}" = "shared"
    Purpose                                     = "eks-pod"
  }

  depends_on = [aws_vpc_ipv4_cidr_block_association.pod_cidr]
}

resource "aws_route_table_association" "pod" {
  for_each = aws_subnet.pod

  subnet_id      = each.value.id
  route_table_id = aws_route_table.private.id  # 기존 프라이빗 라우팅 테이블 재사용
}

# ─────────────────────────────────────────
# ENIConfig CRD (kubectl_manifest 사용)
# ─────────────────────────────────────────
resource "kubectl_manifest" "eni_config" {
  for_each = aws_subnet.pod

  yaml_body = yamlencode({
    apiVersion = "crd.k8s.amazonaws.com/v1alpha1"
    kind       = "ENIConfig"
    metadata = {
      name = each.key  # AZ 이름 = ENIConfig 이름
    }
    spec = {
      subnet         = each.value.id
      securityGroups = [aws_security_group.pod.id]
    }
  })

  depends_on = [aws_eks_cluster.main]
}

# ─────────────────────────────────────────
# VPC CNI Addon — Custom Networking + Prefix Delegation
# ─────────────────────────────────────────
resource "aws_eks_addon" "vpc_cni" {
  cluster_name = aws_eks_cluster.main.name
  addon_name   = "vpc-cni"

  # IRSA가 있어야 aws-node가 ENI 생성 API 호출 가능
  service_account_role_arn = aws_iam_role.vpc_cni.arn

  configuration_values = jsonencode({
    env = {
      AWS_VPC_K8S_CNI_CUSTOM_NETWORK_CFG = "true"
      ENI_CONFIG_LABEL_DEF               = "topology.kubernetes.io/zone"
      ENABLE_PREFIX_DELEGATION           = "true"
      WARM_PREFIX_TARGET                 = "1"
    }
  })

  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"
}
```

---

## 6. 설정 검증

### 6.1 Pod IP가 실제로 Pod 서브넷에서 할당됐는지 확인

```bash
# Pod IP 목록 조회
kubectl get pods -A -o wide | awk 'NR>1 {print $8}' | sort -u | head -20
# 100.64.x.x 대역이면 Custom Networking 정상 동작

# 특정 Pod IP 확인
kubectl get pod my-pod -o jsonpath='{.status.podIP}'

# 노드별 Pod IP 서브넷 확인
kubectl get pods -A -o wide --no-headers | \
  awk '{print $8, $7}' | sort -k2 | \
  awk '{
    split($1, ip, ".");
    print "Pod: " $1 " → Node: " $2 " → Subnet: " ip[1]"."ip[2]"."ip[3]".0/24"
  }'
```

### 6.2 aws-node가 ENIConfig를 올바르게 읽는지 확인

```bash
# aws-node 로그에서 ENIConfig 참조 확인
kubectl logs -n kube-system \
  $(kubectl get pod -n kube-system -l k8s-app=aws-node -o jsonpath='{.items[0].metadata.name}') \
  | grep -i "ENIConfig\|custom.*network\|subnet"

# 정상 로그 예시:
# Using ENIConfig: ap-northeast-2a
# Assigned pod IP 100.64.0.5 from subnet subnet-pod-2a-xxx
```

### 6.3 노드의 ENI 구성 확인

```bash
# 노드의 ENI 목록 확인 (Secondary ENI가 Pod 서브넷에 있어야 함)
NODE_IP=$(kubectl get node <node-name> -o jsonpath='{.status.addresses[0].address}')
INSTANCE_ID=$(aws ec2 describe-instances \
  --filters "Name=private-ip-address,Values=${NODE_IP}" \
  --query 'Reservations[0].Instances[0].InstanceId' --output text)

aws ec2 describe-network-interfaces \
  --filters "Name=attachment.instance-id,Values=${INSTANCE_ID}" \
  --query 'NetworkInterfaces[*].{
    ENI:NetworkInterfaceId,
    SubnetId:SubnetId,
    PrivateIP:PrivateIpAddress,
    DeviceIndex:Attachment.DeviceIndex
  }' --output table
# DeviceIndex=0 → 노드 서브넷 (eth0)
# DeviceIndex=1,2,... → Pod 서브넷 (Custom Networking 서브넷)
```

### 6.4 ENIConfig 매핑 확인

```bash
# 노드 라벨과 ENIConfig 이름이 일치하는지 교차 확인
echo "=== 노드 AZ 라벨 ==="
kubectl get nodes -o custom-columns=\
  'NODE:.metadata.name,AZ:.metadata.labels.topology\.kubernetes\.io/zone'

echo "=== ENIConfig 목록 ==="
kubectl get eniconfigs -o custom-columns=\
  'NAME:.metadata.name,SUBNET:.spec.subnet'
```

---

## 7. ENI_CONFIG_LABEL_DEF 매핑 방식 3가지

`ENI_CONFIG_LABEL_DEF`로 ENIConfig 이름을 찾는 방식은 세 가지가 있다.

### 방식 1: topology.kubernetes.io/zone (권장, 자동)

```bash
ENI_CONFIG_LABEL_DEF=topology.kubernetes.io/zone
```
- EKS가 노드 기동 시 자동으로 붙이는 라벨 사용
- ENIConfig 이름 = AZ 이름 (ap-northeast-2a)
- **추가 작업 없이 동작** — 표준 패턴

### 방식 2: 커스텀 노드 라벨 (멀티 클러스터 분리)

```bash
ENI_CONFIG_LABEL_DEF=k8s.amazonaws.com/eniConfig
```

노드마다 직접 라벨 지정:
```bash
kubectl label node <node-name> k8s.amazonaws.com/eniConfig=ap-northeast-2a-cluster-a
```

같은 AZ에서도 클러스터별로 다른 서브넷을 사용해야 할 때 유용하다.

### 방식 3: ENI_CONFIG_ANNOTATION_DEF (노드 어노테이션)

```bash
ENI_CONFIG_ANNOTATION_DEF=k8s.amazonaws.com/eniConfig
```

노드 어노테이션으로 ENIConfig 이름 직접 지정. 라벨 오염을 피하고 싶은 경우.

---

## 8. 트러블슈팅

### Pod가 여전히 노드 서브넷 IP를 받는 경우

```bash
# 1. aws-node 환경변수 적용 확인
kubectl get daemonset aws-node -n kube-system \
  -o jsonpath='{.spec.template.spec.containers[0].env}' | \
  python3 -c "import sys,json; [print(e['name'],'=',e.get('value','')) for e in json.load(sys.stdin)]"

# 2. ENIConfig 이름과 노드 라벨 값 불일치 확인
kubectl get nodes -L topology.kubernetes.io/zone
kubectl get eniconfigs

# 3. aws-node 재시작 (설정 변경 후 반영)
kubectl rollout restart daemonset aws-node -n kube-system

# 4. 노드가 교체됐는지 확인 (기존 노드는 새 ENI 설정 미적용)
kubectl get nodes -o wide
# 설정 변경 이전에 시작된 노드는 재생성 필요
```

### ENIConfig 서브넷에 IP가 부족한 경우

```bash
# Pod 서브넷 가용 IP 확인
aws ec2 describe-subnets \
  --filters "Name=tag:Purpose,Values=eks-pod" \
  --query 'Subnets[*].{AZ:AvailabilityZone,CIDR:CidrBlock,AvailableIPs:AvailableIpAddressCount}' \
  --output table

# IP 부족 시 대응:
# 1. Prefix Delegation 활성화 (IP 효율 향상)
# 2. Pod 서브넷 CIDR 확장 불가 → 새 서브넷 추가 후 ENIConfig 교체
# 3. 100.64.0.0/16 범위 내 추가 /19 서브넷 생성
```

### ipamd(aws-node)가 ENI를 붙이지 못하는 경우

```bash
# aws-node IRSA 권한 확인
kubectl describe sa aws-node -n kube-system | grep Annotations

# 필요 IAM 권한 (최소)
# ec2:CreateNetworkInterface
# ec2:AttachNetworkInterface
# ec2:DeleteNetworkInterface
# ec2:DetachNetworkInterface
# ec2:DescribeNetworkInterfaces
# ec2:DescribeSubnets
# ec2:DescribeSecurityGroups
# ec2:ModifyNetworkInterfaceAttribute

# aws-node 상세 오류 로그
kubectl logs -n kube-system -l k8s-app=aws-node --tail=100 | grep -i error
```

---

## 9. 모니터링

```hcl
# Pod 서브넷 가용 IP 부족 알람
resource "aws_cloudwatch_metric_alarm" "pod_subnet_ip_low" {
  for_each = {
    "2a" = "subnet-pod-2a-xxxxxxxx"
    "2b" = "subnet-pod-2b-xxxxxxxx"
    "2c" = "subnet-pod-2c-xxxxxxxx"
  }

  alarm_name          = "eks-pod-subnet-ip-low-${each.key}"
  alarm_description   = "Pod 서브넷(AZ-${each.key}) 가용 IP 50개 미만"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 3
  metric_name         = "SubnetAvailableIpCount"
  namespace           = "AWS/EC2"   # 또는 ContainerInsights
  period              = 300
  statistic           = "Minimum"
  threshold           = 50

  dimensions = {
    SubnetId = each.value
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
}
```

---

## 10. 설계 체크리스트

```
[ ] Secondary CIDR (100.64.0.0/10 권장)을 VPC에 추가했는가?
[ ] AZ별 Pod 서브넷을 생성하고 라우팅 테이블을 연결했는가?
[ ] Pod 서브넷에 kubernetes.io/cluster/<name>=shared 태그를 붙였는가?
[ ] ENIConfig CRD를 AZ별로 생성하고 이름이 AZ 이름과 일치하는가?
[ ] ENIConfig의 securityGroups가 Pod 통신에 필요한 규칙을 포함하는가?
[ ] AWS_VPC_K8S_CNI_CUSTOM_NETWORK_CFG=true 를 적용했는가?
[ ] ENI_CONFIG_LABEL_DEF=topology.kubernetes.io/zone 을 설정했는가?
[ ] Prefix Delegation을 함께 활성화했는가? (Primary ENI 낭비 보상)
[ ] 기존 노드를 모두 교체(Refresh)했는가?
[ ] kubectl get pod -o wide 로 Pod IP가 100.64.x.x 대역인지 확인했는가?
[ ] Pod 서브넷 가용 IP 모니터링 알람을 설정했는가?
```

---

## 11. TIP

- **100.64.0.0/10은 Direct Connect / VPN 광고 불필요**: 온프레미스 라우팅 테이블에 추가하지 않아도 되는 경우가 많다. Pod IP를 온프레미스에서 직접 접근해야 하는 요구사항이 있다면 BGP 광고 범위를 미리 확인한다
- **Security Groups for Pods와 조합**: Custom Networking 활성화 후 `ENABLE_POD_ENI=true`를 추가하면 Pod 단위 Security Group 적용이 가능하다. 단, Nitro 인스턴스 전용이며 트렁크 ENI를 소비하므로 노드당 Pod 수 계산에 포함해야 한다
- **Fargate는 Custom Networking 불필요**: Fargate Pod는 자동으로 Pod 전용 ENI를 받는다. Custom Networking은 EC2 Managed/Self-managed 노드 전용 설정이다
- **클러스터 생성 전 설정 권장**: 이미 워크로드가 돌고 있는 클러스터에서 Custom Networking을 켜면 전체 노드 교체가 필요하다. 신규 클러스터 또는 신규 노드그룹 추가 시 처음부터 활성화하는 것이 안전하다
