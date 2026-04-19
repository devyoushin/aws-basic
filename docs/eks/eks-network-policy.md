# EKS 네트워크 정책 (Network Policy)

## 1. 개요

Kubernetes NetworkPolicy는 Pod 간 통신을 화이트리스트 방식으로 제어하는 메커니즘이다.
정책을 적용하지 않으면 모든 Pod가 서로 통신 가능 (기본 all-allow).
EKS에서는 VPC CNI Network Policy 플러그인(AWS 네이티브) 또는 Calico를 사용한다.

---

## 2. 설명

### 2.1 핵심 개념

**기본 동작 원리**

- **정책 없음**: Pod 간 모든 통신 허용
- **ingress 정책 적용**: 명시적으로 허용된 소스에서 오는 트래픽만 허용
- **egress 정책 적용**: 명시적으로 허용된 대상으로만 트래픽 전송 가능
- **정책 선택자(podSelector)**: 빈 `{}`이면 해당 namespace의 모든 Pod에 적용

**VPC CNI Network Policy vs Calico 비교**

| 항목 | VPC CNI Network Policy | Calico |
|------|----------------------|--------|
| AWS 네이티브 | 예 | 아니오 (서드파티) |
| 설치 복잡도 | 낮음 (add-on) | 중간 |
| 성능 | eBPF 기반 | iptables 또는 eBPF |
| 고급 정책 (CIDR, DNS) | 제한적 | 풍부한 기능 (GlobalNetworkPolicy 등) |
| 지원 Kubernetes 버전 | EKS 1.25+ | 광범위 |

---

### 2.2 실무 적용 코드

**VPC CNI Network Policy 활성화**

```bash
# EKS 1.25 이상에서 VPC CNI add-on에 Network Policy 활성화
aws eks update-addon \
  --cluster-name my-cluster \
  --addon-name vpc-cni \
  --configuration-values '{"enableNetworkPolicy": "true"}'

# 또는 Terraform
resource "aws_eks_addon" "vpc_cni" {
  cluster_name = aws_eks_cluster.main.name
  addon_name   = "vpc-cni"

  configuration_values = jsonencode({
    enableNetworkPolicy = "true"
  })
}
```

**패턴 1: Default Deny All (기본 거부 정책)**

```yaml
# 모든 ingress 차단 (namespace에 먼저 적용)
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-ingress
  namespace: production
spec:
  podSelector: {}     # 모든 Pod 대상
  policyTypes:
    - Ingress          # ingress 정책만 적용 (egress는 여전히 허용)
---
# 모든 egress 차단
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-egress
  namespace: production
spec:
  podSelector: {}
  policyTypes:
    - Egress
```

**패턴 2: 특정 Pod만 DB 접근 허용**

```yaml
# DB Pod에 적용 — app=backend에서 오는 3306 트래픽만 허용
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-backend-to-db
  namespace: production
spec:
  podSelector:
    matchLabels:
      app: mysql           # 이 정책이 적용될 Pod
  policyTypes:
    - Ingress
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app: backend  # backend Pod만 허용
      ports:
        - protocol: TCP
          port: 3306
```

**패턴 3: namespace 간 통신 허용**

```yaml
# production namespace의 특정 Pod가 monitoring namespace의 Prometheus에 접근 허용
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-prometheus-scrape
  namespace: production
spec:
  podSelector: {}       # production의 모든 Pod
  policyTypes:
    - Ingress
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: monitoring
          podSelector:
            matchLabels:
              app: prometheus
      ports:
        - protocol: TCP
          port: 8080    # 메트릭 포트
```

**패턴 4: DNS 허용 + 외부 egress 제한**

```yaml
# default-deny-egress 이후 DNS와 특정 외부만 허용
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-dns-and-external
  namespace: production
spec:
  podSelector:
    matchLabels:
      app: my-app
  policyTypes:
    - Egress
  egress:
    # CoreDNS 허용 (필수 — 없으면 서비스 이름 조회 불가)
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
          podSelector:
            matchLabels:
              k8s-app: kube-dns
      ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
    # 내부 서비스 허용
    - to:
        - podSelector:
            matchLabels:
              app: mysql
      ports:
        - protocol: TCP
          port: 3306
    # 특정 외부 CIDR 허용 (예: Secrets Manager VPC Endpoint)
    - to:
        - ipBlock:
            cidr: 10.0.0.0/8   # VPC 내부 트래픽
      ports:
        - protocol: TCP
          port: 443
```

**패턴 5: 마이크로세그멘테이션 설계**

```yaml
# 계층별 통신 허용 (frontend → backend → db)
# frontend는 backend로만, backend는 db로만, db는 아무것도 시작 불가

# backend 정책
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: backend-policy
  namespace: production
spec:
  podSelector:
    matchLabels:
      tier: backend
  policyTypes:
    - Ingress
    - Egress
  ingress:
    - from:
        - podSelector:
            matchLabels:
              tier: frontend
      ports:
        - port: 8080
  egress:
    - to:
        - podSelector:
            matchLabels:
              tier: database
      ports:
        - port: 5432
    # DNS 허용
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
      ports:
        - port: 53
          protocol: UDP
```

---

### 2.3 보안/비용 Best Practice

- **Default Deny를 먼저 적용 후 필요한 것만 허용**: 반대로 하면 누락 위험
- **DNS egress 허용 필수**: default-deny-egress 적용 시 CoreDNS(UDP/TCP 53)를 반드시 화이트리스트에 추가
- **네임스페이스 레이블 활용**: `kubernetes.io/metadata.name` 레이블은 Kubernetes 1.21+에서 자동 추가
- **정책 적용 전 audit 모드로 검증**: VPC Flow Logs로 예상 트래픽 패턴 파악 후 정책 수립

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**정책 적용 후 서비스 통신 끊김**

```bash
# Pod 간 연결 테스트
kubectl exec -it frontend-pod -n production -- \
  wget -qO- http://backend-service:8080/health

# NetworkPolicy 목록 확인
kubectl get networkpolicy -n production

# 특정 Pod에 적용된 정책 확인
kubectl get networkpolicy -n production -o yaml | \
  grep -A 20 "podSelector"

# 흔한 원인: podSelector 레이블 오타
kubectl get pod frontend-pod -n production --show-labels
```

**DNS 조회 실패 (default-deny-egress 후)**

```bash
# Pod에서 DNS 조회 실패
kubectl exec -it my-app-pod -n production -- nslookup kubernetes.default
# ;; connection timed out; no servers could be reached

# 원인: CoreDNS로의 UDP 53 egress 차단
# 해결: DNS egress 허용 정책 추가 (위 패턴 4 참고)
```

**namespaceSelector가 작동 안 함**

```bash
# namespace 레이블 확인
kubectl get namespace production --show-labels
# kubernetes.io/metadata.name=production 레이블이 있어야 함

# 없는 경우 수동 추가 (Kubernetes 1.21 미만)
kubectl label namespace production kubernetes.io/metadata.name=production
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: podSelector에 `{}`를 쓰면 namespace의 모든 Pod가 대상인가요?**
A: 예. `podSelector: {}`는 해당 namespace의 모든 Pod를 선택합니다. 다른 namespace의 Pod는 포함하지 않습니다.

**Q: AND 조건과 OR 조건을 어떻게 구분하나요?**
A: `from` 배열 내 같은 항목의 `namespaceSelector`와 `podSelector`는 AND (둘 다 만족), 별도 항목은 OR입니다.
```yaml
# AND: monitoring namespace의 prometheus Pod만
- from:
    - namespaceSelector:
        matchLabels:
          name: monitoring
      podSelector:
        matchLabels:
          app: prometheus

# OR: monitoring namespace 모든 Pod 또는 prometheus 레이블 Pod
- from:
    - namespaceSelector:
        matchLabels:
          name: monitoring
    - podSelector:
        matchLabels:
          app: prometheus
```

---

## 4. 모니터링 및 알람

```bash
# VPC Flow Logs로 REJECT된 Pod 간 트래픽 확인
# Athena 쿼리
SELECT srcaddr, dstaddr, srcport, dstport, action, count(*) as cnt
FROM vpc_flow_logs
WHERE action = 'REJECT'
  AND partition_date >= '2024/01/01'
GROUP BY srcaddr, dstaddr, srcport, dstport, action
ORDER BY cnt DESC
LIMIT 50;
```

```hcl
# NetworkPolicy 변경 감지 알람 (CloudTrail)
resource "aws_cloudwatch_event_rule" "network_policy_change" {
  name = "eks-network-policy-modified"

  event_pattern = jsonencode({
    source      = ["aws.eks"]
    detail-type = ["EKS API Call via CloudTrail"]
    detail = {
      eventName = ["CreateNetworkPolicy", "DeleteNetworkPolicy", "PatchNetworkPolicy"]
    }
  })
}
```

---

## 5. TIP

- **정책 시각화 도구**: `netpol` CLI, Cilium 네트워크 정책 에디터 (GUI)로 복잡한 정책 시각화 가능
- **정책 테스트**: `kubectl exec` + `curl`/`nc`로 연결 허용/거부 직접 검증
  ```bash
  # TCP 연결 테스트
  kubectl exec -it test-pod -- nc -zv mysql-service 3306
  # 성공: Connection to mysql-service 3306 port [tcp/mysql] succeeded!
  # 실패: nc: connect to mysql-service port 3306 (tcp) timed out
  ```
- **Calico GlobalNetworkPolicy**: 클러스터 전체에 적용되는 정책 (네임스페이스 구분 없음) — 비표준 CRD이므로 Calico 설치 필요
