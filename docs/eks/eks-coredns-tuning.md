# CoreDNS 성능 튜닝 (EKS)

## 1. 개요

EKS에서 DNS 관련 레이턴시 문제의 가장 흔한 원인은 `ndots:5` 기본 설정이다.
Pod에서 외부 도메인(예: `api.example.com`)을 조회할 때
Kubernetes가 search domain 조합으로 최대 6번 조회를 시도하기 때문에
DNS 응답이 5초 지연되는 현상이 발생한다.

---

## 2. 설명

### 2.1 핵심 개념

**ndots:5 로 인한 DNS 6번 조회 과정**

`api.example.com` 조회 시 (점이 2개 → ndots:5 조건 미충족):

```
1. api.example.com.production.svc.cluster.local  → NXDOMAIN
2. api.example.com.svc.cluster.local             → NXDOMAIN
3. api.example.com.cluster.local                 → NXDOMAIN
4. api.example.com.ap-northeast-2.compute.internal → NXDOMAIN
5. api.example.com.ap-northeast-2.compute.amazonaws.com → NXDOMAIN (일부 환경)
6. api.example.com.                              → 성공 (실제 DNS 조회)
```

→ 첫 5번 조회가 실패하면서 총 수 초~수십 초 지연 발생

**핵심 해결 방법 3가지**

| 방법 | 적용 범위 | 효과 |
|------|---------|------|
| FQDN 사용 (도메인 끝에 `.`) | 개별 호출 | 즉시, 코드 변경 필요 |
| `ndots` 값 줄이기 | Pod/Deployment | 불필요한 조회 감소 |
| NodeLocal DNSCache | 클러스터 전체 | 캐시 히트율 향상, DNS 성능 개선 |

**autopath 플러그인**

CoreDNS의 autopath 플러그인은 search domain 조회를 CoreDNS 수준에서 처리하여
클라이언트가 여러 번 조회하지 않아도 되게 한다. 단, CoreDNS 부하가 증가할 수 있다.

---

### 2.2 실무 적용 코드

**방법 1: Pod spec에 dnsConfig 설정**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-app
  namespace: production
spec:
  template:
    spec:
      dnsPolicy: ClusterFirst
      dnsConfig:
        options:
          - name: ndots
            value: "2"    # 기본 5 → 2로 줄이기
                          # 점이 2개 이하인 도메인만 search domain 조합
          - name: single-request-reopen
            # IPv4/IPv6 동시 조회 시 race condition 방지
      containers:
        - name: app
          image: my-app:latest
```

**방법 2: NodeLocal DNSCache 배포 (클러스터 전체 적용)**

```bash
# NodeLocal DNSCache 설치 (각 노드에 DaemonSet으로 배포)
# CoreDNS 앞단에 로컬 캐시를 두어 반복 조회를 캐시에서 처리

# 공식 매니페스트 다운로드 및 적용
NODE_LOCAL_DNS_IP="169.254.20.10"   # 링크-로컬 IP 사용 (권장)

curl -O https://raw.githubusercontent.com/kubernetes/kubernetes/master/cluster/addons/dns/nodelocaldns/nodelocaldns.yaml

sed -i "s/__PILLAR__LOCAL__DNS__/$NODE_LOCAL_DNS_IP/g" nodelocaldns.yaml
sed -i "s/__PILLAR__DNS__SERVER__/$(kubectl get svc kube-dns -n kube-system -o jsonpath='{.spec.clusterIP}')/g" nodelocaldns.yaml
sed -i "s/__PILLAR__DNS__DOMAIN__/cluster.local/g" nodelocaldns.yaml

kubectl apply -f nodelocaldns.yaml

# DaemonSet 배포 확인
kubectl get daemonset node-local-dns -n kube-system
```

**방법 3: CoreDNS Corefile 커스터마이징**

```bash
# 현재 Corefile 확인
kubectl get configmap coredns -n kube-system -o yaml
```

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: coredns
  namespace: kube-system
data:
  Corefile: |
    .:53 {
        errors
        health {
            lameduck 5s
        }
        ready
        kubernetes cluster.local in-addr.arpa ip6.arpa {
            pods insecure
            fallthrough in-addr.arpa ip6.arpa
            ttl 30
        }
        # autopath 플러그인 추가 (search domain 클라이언트 왕복 감소)
        autopath @kubernetes
        prometheus :9153
        forward . /etc/resolv.conf {
            max_concurrent 1000
        }
        # 캐시 TTL 증가 (기본 30초 → 300초)
        cache 300
        loop
        reload
        loadbalance
    }
```

```bash
# Corefile 수정 후 CoreDNS 재시작
kubectl rollout restart deployment coredns -n kube-system
```

**CoreDNS 수평 확장**

```bash
# CoreDNS replica 수 증가 (대규모 클러스터)
kubectl scale deployment coredns --replicas=4 -n kube-system

# 또는 cluster-proportional-autoscaler로 노드 수에 비례해 자동 확장
```

**DNS 동작 검증**

```bash
# Pod 내부에서 DNS 조회 과정 확인
kubectl run -it --rm dns-test --image=busybox --restart=Never -- sh

# Pod 내부에서
nslookup api.example.com      # 6번 조회 발생 가능
nslookup api.example.com.     # FQDN — 즉시 조회 (점으로 끝남)

# /etc/resolv.conf 확인
cat /etc/resolv.conf
# nameserver 172.20.0.10
# search production.svc.cluster.local svc.cluster.local cluster.local
# options ndots:5    ← 이 값이 문제

# dig로 응답 시간 측정
dig api.example.com +stats | grep "Query time"
dig api.example.com. +stats | grep "Query time"   # FQDN — 훨씬 빠름
```

---

### 2.3 보안/비용 Best Practice

- **외부 도메인 호출 시 FQDN 사용 권장**: 코드에서 `api.example.com.`처럼 끝에 점 추가
- **ndots: 2로 설정**: Kubernetes 내부 서비스 조회(`my-svc.namespace`)는 영향 없이 외부 도메인 불필요한 조회 제거
- **CoreDNS 고가용성**: 최소 `replicas: 2` 유지, `PodAntiAffinity`로 서로 다른 노드에 배치
- **NodeLocal DNSCache**: 대규모 클러스터에서 CoreDNS 부하를 획기적으로 감소시킴

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**특정 외부 도메인만 느린 케이스**

```bash
# 조회 시간 측정
time nslookup slow.external.com

# CoreDNS 업스트림 DNS 서버 응답 확인
kubectl exec -n kube-system deployment/coredns -- \
  nslookup slow.external.com 8.8.8.8

# 특정 도메인만 다른 DNS 서버로 포워딩
# Corefile에 추가:
# slow-domain.com:53 {
#     forward . 1.1.1.1
# }
```

**CoreDNS CrashLoopBackOff**

```bash
# CoreDNS 로그 확인
kubectl logs -n kube-system deployment/coredns

# 흔한 원인: Corefile 문법 오류
# ConfigMap 수정 후 문법 검사
kubectl exec -n kube-system deployment/coredns -- \
  /coredns -conf /etc/coredns/Corefile -dry-run

# CoreDNS 재시작
kubectl rollout restart deployment coredns -n kube-system
```

**DNS 쿼리 폭주 (CPU 급증)**

```bash
# CoreDNS 지표로 쿼리 수 확인
kubectl port-forward -n kube-system svc/kube-dns 9153:9153

curl http://localhost:9153/metrics | grep coredns_dns_requests_total

# 쿼리 폭주 원인: 짧은 TTL의 외부 도메인 반복 조회
# 해결: cache TTL 증가, NodeLocal DNSCache 적용
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: ndots:2로 바꾸면 Kubernetes 내부 서비스 조회가 안 될 수 있나요?**
A: `my-service.my-namespace` (점 1개)는 ndots:2 조건에 해당하여 search domain을 거칩니다. 하지만 `my-service.my-namespace.svc.cluster.local` (점 4개)는 FQDN으로 바로 조회됩니다. 내부 서비스는 짧은 이름을 그대로 사용해도 동작합니다.

**Q: NodeLocal DNSCache 설치 후 기존 Pod를 재시작해야 하나요?**
A: 예. 기존 Pod는 이전 `nameserver`를 캐싱하고 있어 NodeLocal DNSCache를 사용하지 않습니다. `kubectl rollout restart deployment` 또는 노드 교체 시 새 Pod가 자동으로 NodeLocal DNSCache를 사용합니다.

---

## 4. 모니터링 및 알람

```yaml
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: coredns-alerts
  namespace: monitoring
spec:
  groups:
    - name: coredns.rules
      rules:
        - alert: CoreDNSHighLatency
          expr: |
            histogram_quantile(0.99,
              rate(coredns_dns_request_duration_seconds_bucket[5m])
            ) > 1
          for: 5m
          labels:
            severity: warning
          annotations:
            summary: "CoreDNS 99분위 응답 시간 1초 초과"

        - alert: CoreDNSHighErrorRate
          expr: |
            rate(coredns_dns_responses_total{rcode="SERVFAIL"}[5m]) > 0.1
          for: 5m
          labels:
            severity: critical
          annotations:
            summary: "CoreDNS SERVFAIL 응답 급증"
```

**주요 CoreDNS Prometheus 지표**

| 지표 | 의미 |
|------|------|
| `coredns_dns_request_duration_seconds` | 요청 응답 시간 (histogram) |
| `coredns_dns_responses_total` | 응답 코드별 카운트 (NOERROR, NXDOMAIN, SERVFAIL) |
| `coredns_cache_hits_total` | 캐시 히트 수 |
| `coredns_cache_misses_total` | 캐시 미스 수 |
| `coredns_forward_requests_total` | 업스트림으로 포워딩된 요청 수 |

---

## 5. TIP

- **빠른 진단**: `kubectl run -it --rm dns-debug --image=infoblox/dnstools --restart=Never` — DNS 도구가 포함된 디버그 Pod
- **dnsperf 벤치마크**: CoreDNS 성능 측정 도구 (`dnsperf -s <coredns-ip> -d /tmp/queries.txt`)
- **search domain 조회 흐름 로그**:
  ```bash
  # CoreDNS에서 모든 쿼리 로깅 (디버깅 용, 프로덕션에서는 부하 주의)
  # Corefile에 log 플러그인 추가
  .:53 {
    log   # 모든 DNS 쿼리 로깅
    ...
  }
  ```
