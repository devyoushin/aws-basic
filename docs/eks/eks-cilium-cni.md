# EKS Cilium CNI — VPC CNI 대체 전략 및 마이그레이션

## 1. 개요

**Cilium**은 eBPF 기반의 오픈소스 CNI로, AWS VPC CNI 대비 고급 네트워크 정책,
서비스 메시 기능, 고성능 데이터 플레인, Hubble 관측성을 제공한다.
EKS에서 VPC CNI를 Cilium으로 교체하면 IP 주소 관리 방식, 네트워크 정책 엔진,
kube-proxy 교체(eBPF) 등 클러스터 네트워킹이 근본적으로 바뀐다.

**핵심 요약**
- **사용 목적**: 고급 L7 네트워크 정책, eBPF kube-proxy 대체, 멀티 클러스터 메시
- **주요 이점**: 낮은 레이턴시, 세분화된 보안 정책, Hubble 관측성
- **관련 서비스**: EKS, VPC, IAM, ECR, CloudWatch

---

## 2. 설명

### 2.1 VPC CNI vs Cilium 비교

```
[VPC CNI 방식]
Pod ← Secondary IP (VPC 실제 IP) ← ENI ← 서브넷 IP 소비
  └── L3 정책만 (NetworkPolicy)
  └── kube-proxy: iptables/ipvs

[Cilium 방식 - Overlay]
Pod ← 가상 IP (VXLAN/Geneve 오버레이) ← 언더레이(VPC IP)
  └── L3/L4/L7 정책 (HTTP, gRPC, Kafka)
  └── kube-proxy 제거 → eBPF 직접 처리

[Cilium 방식 - ENI 모드 (VPC Native)]
Pod ← Secondary IP (VPC 실제 IP) ← ENI (Cilium이 관리)
  └── VPC CNI와 동일한 IP 모델 + Cilium 정책 엔진
```

| 항목 | VPC CNI | Cilium (Overlay) | Cilium (ENI) |
|------|---------|-----------------|-------------|
| Pod IP | VPC 실제 IP | 오버레이 가상 IP | VPC 실제 IP |
| IP 소비 | 높음 (VPC 서브넷) | 낮음 (오버레이) | 높음 (VPC 서브넷) |
| 네트워크 정책 | L3/L4 기본 | L3/L4/L7 풍부 | L3/L4/L7 풍부 |
| kube-proxy | 필요 | eBPF 대체 가능 | eBPF 대체 가능 |
| 성능 | 좋음 | 우수 (eBPF) | 우수 (eBPF) |
| VPC 직접 라우팅 | 가능 | 터널링 필요 | 가능 |
| 운영 복잡도 | 낮음 | 중간 | 중간 |
| Hubble 관측성 | 없음 | 있음 | 있음 |

---

### 2.2 Cilium IPAM 모드 선택

#### 모드 1: Cluster Pool (Overlay — 권장 시작점)

```
클러스터 내부 IP 풀에서 Pod IP 할당
- 기본 Pod CIDR: 10.0.0.0/8 (클러스터 전용)
- VPC 서브넷 IP 소비 없음
- VPC 외부에서 Pod IP 직접 접근 불가 (터널링 필요)
```

```yaml
# Cilium Helm values — Cluster Pool 모드
ipam:
  mode: cluster-pool
  operator:
    clusterPoolIPv4PodCIDRList:
      - "10.0.0.0/8"
    clusterPoolIPv4MaskSize: 24   # 노드당 /24 블록 (254 Pod/노드)
tunnel: vxlan
```

#### 모드 2: AWS ENI 모드 (VPC Native)

```
Cilium이 ENI를 직접 관리하여 VPC IP를 Pod에 할당
- VPC CNI와 동일한 IP 모델
- VPC 내에서 Pod IP 직접 라우팅 가능
- 인스턴스 타입별 ENI/IP 한도 동일하게 적용
```

```yaml
# Cilium Helm values — ENI 모드
ipam:
  mode: eni
eni:
  enabled: true
  awsEnablePrefixDelegation: true  # Prefix Delegation 지원
  instanceType: ""                  # 자동 감지
tunnel: disabled
autoDirectNodeRoutes: true
```

---

### 2.3 마이그레이션 전략

EKS에서 VPC CNI → Cilium 마이그레이션은 **인-플레이스(in-place) 교체가 불가능**하다.
노드의 CNI를 실시간으로 변경하면 기존 Pod의 네트워크가 끊어진다.
따라서 **새 노드 그룹 방식(Blue-Green)** 또는 **순차 노드 교체 방식**을 사용한다.

#### 전략 A: Blue-Green 노드 그룹 교체 (권장)

```
[기존 클러스터]
Node Group A (VPC CNI)   ← 기존 워크로드
        │
        │  1. Cilium 설치 (VPC CNI와 공존)
        ▼
Node Group B (Cilium)    ← 신규 노드 그룹
        │
        │  2. 워크로드 마이그레이션
        ▼
Node Group A 삭제
```

**Step 1: Cilium 설치 준비**

```bash
# EKS 클러스터 확인
kubectl get nodes
kubectl get pods -n kube-system

# Helm 레포 추가
helm repo add cilium https://helm.cilium.io/
helm repo update

# Cilium 버전 확인
helm search repo cilium/cilium --versions | head -5
```

**Step 2: kube-proxy 비활성화 (Cilium eBPF 대체 시)**

```bash
# kube-proxy DaemonSet 삭제 (Cilium이 대체)
kubectl -n kube-system delete daemonset kube-proxy

# configmap 삭제
kubectl -n kube-system delete configmap kube-proxy
```

> **주의**: kube-proxy 삭제 전 Cilium이 완전히 준비되어야 함.
> 기존 VPC CNI 노드에서는 kube-proxy를 유지하고, Cilium 노드만 eBPF 모드 사용.

**Step 3: Cilium 설치 (Overlay 모드)**

```bash
# EKS 클러스터 정보 조회
CLUSTER_NAME="my-eks-cluster"
REGION="ap-northeast-2"
K8S_VERSION=$(aws eks describe-cluster \
  --name $CLUSTER_NAME \
  --query 'cluster.version' \
  --output text \
  --region $REGION)

# API Server Endpoint 조회
API_SERVER=$(aws eks describe-cluster \
  --name $CLUSTER_NAME \
  --query 'cluster.endpoint' \
  --output text \
  --region $REGION)

# Cilium 설치 — Overlay 모드 (VPC CNI와 공존 가능)
helm install cilium cilium/cilium \
  --version 1.16.0 \
  --namespace kube-system \
  --set eni.enabled=false \
  --set ipam.mode=cluster-pool \
  --set ipam.operator.clusterPoolIPv4PodCIDRList="100.64.0.0/10" \
  --set ipam.operator.clusterPoolIPv4MaskSize=24 \
  --set tunnel=vxlan \
  --set kubeProxyReplacement=true \
  --set k8sServiceHost="${API_SERVER#https://}" \
  --set k8sServicePort=443 \
  --set hubble.enabled=true \
  --set hubble.relay.enabled=true \
  --set hubble.ui.enabled=true
```

**Step 4: Cilium 전용 노드 그룹 생성**

```hcl
# Terraform — Cilium 노드 그룹
resource "aws_eks_node_group" "cilium" {
  cluster_name    = aws_eks_cluster.main.name
  node_group_name = "cilium-nodes"
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = var.private_subnet_ids

  scaling_config {
    desired_size = 3
    max_size     = 10
    min_size     = 1
  }

  launch_template {
    id      = aws_launch_template.cilium_node.id
    version = "$Latest"
  }

  labels = {
    "cilium.io/cni" = "cilium"
  }

  taint {
    key    = "cilium"
    value  = "true"
    effect = "NO_SCHEDULE"
  }

  tags = {
    Name        = "cilium-node"
    Environment = "prod"
    ManagedBy   = "terraform"
  }
}
```

```bash
# 새 노드 그룹에 VPC CNI 비활성화 (userdata)
# Launch Template UserData에 추가:
cat <<'EOF' > /tmp/cilium-userdata.sh
#!/bin/bash
# VPC CNI aws-node DaemonSet이 이 노드에 스케줄되지 않도록 처리
# (노드가 조인된 후 aws-node Pod가 뜨기 전에 제거)
kubectl taint node $(hostname) node.cilium.io/agent-not-ready:NoSchedule-
EOF
```

**Step 5: aws-node DaemonSet을 Cilium 노드에서 제외**

```bash
# aws-node DaemonSet에 NodeSelector 추가
# Cilium 노드에 aws-node Pod가 스케줄되지 않도록 설정
kubectl patch daemonset aws-node \
  -n kube-system \
  --type='json' \
  -p='[{"op":"replace","path":"/spec/template/spec/affinity","value":{"nodeAffinity":{"requiredDuringSchedulingIgnoredDuringExecution":{"nodeSelectorTerms":[{"matchExpressions":[{"key":"cilium.io/cni","operator":"NotIn","values":["cilium"]}]}]}}}}]'
```

**Step 6: 워크로드를 Cilium 노드로 이전**

```bash
# 기존 VPC CNI 노드를 순차적으로 드레인
for NODE in $(kubectl get nodes -l '!cilium.io/cni' -o name); do
  echo "Draining $NODE..."
  kubectl drain $NODE \
    --ignore-daemonsets \
    --delete-emptydir-data \
    --force \
    --grace-period=60
  sleep 30
done

# Cilium 노드 taint 제거 (워크로드 스케줄 허용)
kubectl taint nodes -l 'cilium.io/cni=cilium' cilium:NoSchedule-

# 워크로드 재배포 확인
kubectl get pods -A -o wide | grep -v kube-system
```

**Step 7: 기존 VPC CNI 노드 그룹 삭제**

```bash
# 노드 그룹 삭제 전 모든 워크로드 이전 확인
kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded

# EKS 노드 그룹 삭제
aws eks delete-nodegroup \
  --cluster-name $CLUSTER_NAME \
  --nodegroup-name "vpc-cni-nodes" \
  --region $REGION
```

---

#### 전략 B: 신규 클러스터 구성 (가장 안전)

기존 워크로드가 이전 가능한 경우, 처음부터 Cilium으로 구성된 새 EKS 클러스터를 만들고
워크로드를 GitOps/ArgoCD를 통해 이전하는 것이 가장 안전하다.

```bash
# eksctl로 Cilium 전용 클러스터 생성
eksctl create cluster \
  --name cilium-cluster \
  --region ap-northeast-2 \
  --without-nodegroup  # 노드 그룹 없이 클러스터만 생성

# VPC CNI 비활성화 (Managed Addon 삭제)
aws eks delete-addon \
  --cluster-name cilium-cluster \
  --addon-name vpc-cni \
  --region ap-northeast-2

# aws-node DaemonSet 삭제
kubectl delete daemonset aws-node -n kube-system

# Cilium 설치
helm install cilium cilium/cilium \
  --version 1.16.0 \
  --namespace kube-system \
  --set ipam.mode=cluster-pool \
  ...

# 노드 그룹 추가 (Cilium 준비 후)
eksctl create nodegroup \
  --cluster cilium-cluster \
  --name cilium-ng \
  --node-type m5.xlarge \
  --nodes 3
```

---

### 2.4 Hubble 관측성 설정

Cilium의 핵심 가치 중 하나인 Hubble은 eBPF 기반 네트워크 흐름 가시성을 제공한다.

```bash
# Hubble CLI 설치
HUBBLE_VERSION=$(curl -s https://raw.githubusercontent.com/cilium/hubble/master/stable.txt)
curl -L --remote-name-all \
  "https://github.com/cilium/hubble/releases/download/${HUBBLE_VERSION}/hubble-linux-amd64.tar.gz"
tar xzvf hubble-linux-amd64.tar.gz
mv hubble /usr/local/bin/

# Hubble Relay에 포트 포워딩
kubectl port-forward svc/hubble-relay -n kube-system 4245:80 &

# 실시간 네트워크 흐름 확인
hubble observe --follow

# 특정 네임스페이스의 L7 HTTP 흐름
hubble observe \
  --namespace production \
  --protocol http \
  --follow

# 드롭(차단) 흐름만 확인
hubble observe \
  --verdict DROPPED \
  --follow
```

---

### 2.5 Cilium 네트워크 정책 (L7 예시)

VPC CNI의 기본 NetworkPolicy 대비 Cilium은 L7(HTTP/gRPC) 정책을 지원한다.

```yaml
# HTTP 메서드 레벨 정책 (VPC CNI 불가, Cilium 전용)
apiVersion: cilium.io/v2
kind: CiliumNetworkPolicy
metadata:
  name: api-policy
  namespace: production
spec:
  endpointSelector:
    matchLabels:
      app: backend-api
  ingress:
    - fromEndpoints:
        - matchLabels:
            app: frontend
      toPorts:
        - ports:
            - port: "8080"
              protocol: TCP
          rules:
            http:
              - method: GET
                path: "/api/v1/.*"
              - method: POST
                path: "/api/v1/data"
  # 그 외 모든 HTTP 요청 차단
```

```yaml
# DNS 기반 Egress 정책
apiVersion: cilium.io/v2
kind: CiliumNetworkPolicy
metadata:
  name: allow-external-api
spec:
  endpointSelector:
    matchLabels:
      app: data-processor
  egress:
    - toFQDNs:
        - matchName: "api.example.com"
        - matchPattern: "*.amazonaws.com"
      toPorts:
        - ports:
            - port: "443"
              protocol: TCP
```

---

### 2.6 보안/비용 Best Practice

**보안**
- Cilium 마이그레이션 후 기존 NetworkPolicy는 그대로 유지됨 (하위 호환)
- CiliumNetworkPolicy(L7)를 점진적으로 추가 — 기존 정책과 AND 조건으로 동작
- Hubble을 통해 의도치 않은 트래픽 흐름 탐지 후 정책 강화

**비용**
- Overlay 모드: VPC IP 소비 감소 (Pod IP가 오버레이) → 서브넷 CIDR 절약
- kube-proxy 제거 + eBPF: iptables 체인 제거로 CPU 사용률 감소 (대규모 서비스 규칙 처리 시 체감)
- Hubble은 추가 DaemonSet 오버헤드 있음 (노드당 약 256MB 메모리)

---

## 3. 트러블슈팅

### 3.1 주요 이슈

#### Cilium 설치 후 Pod 간 통신 불가

**증상**
- 새 노드에서 Pod가 Running이지만 다른 노드의 Pod와 통신 실패

**원인**
- VPC Security Group이 VXLAN 포트(UDP 8472) 차단
- 또는 aws-node DaemonSet이 Cilium 노드에서도 IP 관리 시도

**해결 방법**
```bash
# Security Group에 VXLAN 허용 (Overlay 모드)
aws ec2 authorize-security-group-ingress \
  --group-id sg-xxxxxxxxxxxxxxxxx \
  --protocol udp \
  --port 8472 \
  --source-group sg-xxxxxxxxxxxxxxxxx \
  --region ap-northeast-2

# Cilium 상태 확인
kubectl exec -n kube-system -it ds/cilium -- cilium status
kubectl exec -n kube-system -it ds/cilium -- cilium connectivity test
```

#### kube-proxy 제거 후 서비스 접근 불가

**증상**
- kube-proxy 삭제 후 ClusterIP 서비스가 응답하지 않음

**원인**
- Cilium의 kube-proxy 대체(eBPF)가 아직 활성화되지 않은 상태에서 kube-proxy를 먼저 삭제

**해결 방법**
```bash
# Cilium kube-proxy 대체 상태 확인
kubectl exec -n kube-system -it ds/cilium -- cilium status | grep -i "kube-proxy"

# eBPF 서비스 맵 확인
kubectl exec -n kube-system -it ds/cilium -- cilium service list

# kube-proxy 임시 재설치 (복구)
# EKS 관리형 addon으로 kube-proxy 재설치
aws eks create-addon \
  --cluster-name $CLUSTER_NAME \
  --addon-name kube-proxy \
  --region $REGION
```

#### ENI 모드에서 IP 할당 실패

**증상**
- Cilium ENI 모드에서 Pod가 Pending 상태, `IP address not available`

**원인**
- Cilium Operator의 IAM 권한 부족 — ENI 생성/IP 할당 권한 필요

**해결 방법**
```json
// IAM Policy 추가 (Cilium Operator용)
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeNetworkInterfaces",
        "ec2:CreateNetworkInterface",
        "ec2:DeleteNetworkInterface",
        "ec2:DescribeInstances",
        "ec2:AttachNetworkInterface",
        "ec2:AssignPrivateIpAddresses",
        "ec2:UnassignPrivateIpAddresses",
        "ec2:DescribeSubnets",
        "ec2:DescribeSecurityGroups"
      ],
      "Resource": "*"
    }
  ]
}
```

```bash
# Cilium Operator 로그 확인
kubectl logs -n kube-system -l app.kubernetes.io/name=cilium-operator --tail=50
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: VPC CNI와 Cilium을 동시에 사용할 수 있나요?**
A: 동시에 같은 노드에서 실행하면 IP 관리 충돌이 발생합니다. Blue-Green 전략으로
노드 그룹을 분리하고, VPC CNI 노드 그룹과 Cilium 노드 그룹을 동시에 운영하면서
점진적으로 이전하는 방식은 가능합니다 (단, Pod 간 터널링 통신 필요).

**Q: Cilium 도입 후 기존 NetworkPolicy가 동작하나요?**
A: 예. Cilium은 Kubernetes 표준 NetworkPolicy를 완전히 지원합니다.
기존 정책은 그대로 유지되고, 추가로 CiliumNetworkPolicy(L7 정책)를 덧붙일 수 있습니다.

**Q: Managed NodeGroup에서 Cilium 사용 가능한가요?**
A: 가능합니다. 단, EKS Managed NodeGroup은 aws-node(VPC CNI) DaemonSet을 자동으로
설치하므로, NodeAffinity/NodeSelector로 Cilium 노드에서 aws-node를 제외해야 합니다.

---

## 4. 모니터링 및 알람

### CloudWatch + Hubble 핵심 지표

| 지표 | 소스 | 의미 | 임계값 예시 |
|------|------|------|------------|
| `cilium_drop_count_total` | Prometheus/CWAgent | 네트워크 정책 드롭 수 | 급증 시 정책 오류 가능성 |
| `cilium_endpoint_state` | Prometheus | Endpoint 상태 (ready/not-ready) | not-ready 증가 |
| `cilium_policy_import_errors_total` | Prometheus | 정책 적용 실패 수 | `> 0` |

```bash
# Cilium Prometheus 메트릭 활성화
helm upgrade cilium cilium/cilium \
  --namespace kube-system \
  --reuse-values \
  --set prometheus.enabled=true \
  --set operator.prometheus.enabled=true \
  --set hubble.metrics.enabled="{dns,drop,tcp,flow,icmp,http}"
```

---

## 5. TIP

- **마이그레이션 전 Cilium 호환성 매트릭스 확인**: EKS K8s 버전 × Cilium 버전 조합 공식 지원 여부 확인 필수
- **Overlay vs ENI 모드 선택 기준**: VPC 외부에서 Pod IP로 직접 접근이 필요하면 ENI 모드, 그렇지 않으면 Overlay(IP 절약, 운영 단순)
- **kube-proxy 대체는 신중하게**: eBPF kube-proxy 대체는 성능이 우수하지만, 일부 커스텀 iptables 규칙이 동작하지 않을 수 있음 — 단계적으로 도입 권장
- **Hubble UI는 운영 초기에 필수**: Cilium 정책 적용 초기에 Hubble로 의도치 않은 차단 흐름을 실시간 확인하여 정책 튜닝
- **cilium connectivity test**: 설치 후 반드시 실행하여 기본 연결성 검증

**관련 문서**
- 연관 내부 문서: `docs/eks/eks-networking-vpc-cni.md`, `docs/eks/eks-network-policy.md`, `docs/eks/eks-eip-ip-strategy.md`
