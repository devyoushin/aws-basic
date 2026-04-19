# EKS Pod CPU/Memory Requests & Limits 설계

## 1. 개요

Kubernetes에서 `resources.requests`는 스케줄링 기준이고, `resources.limits`는 실행 중 사용 제한이다.
잘못 설정하면 OOMKilled, CPU Throttling, 노드 Over-provisioning이 발생한다.
VPA로 실제 사용량 기반 권고값을 수집하고 Prometheus로 측정한 p95/p99 값을 기준으로 설계한다.

---

## 2. 설명

### 2.1 핵심 개념

**requests vs limits**

| 항목 | requests | limits |
|------|---------|--------|
| 역할 | 스케줄러가 노드 선택 기준 | 컨테이너 최대 사용량 제한 |
| CPU 초과 시 | 초과 가능 (노드 여유 있으면) | Throttling (CPU 사용 제한) |
| Memory 초과 시 | 초과 가능 | OOMKilled (프로세스 강제 종료) |
| 노드 리소스 | requests 합계로 node capacity 측정 | limits은 오버커밋 가능 |

**Quality of Service (QoS) 클래스**

| QoS | 조건 | 특성 |
|-----|------|------|
| `Guaranteed` | requests == limits (CPU, Memory 모두) | 메모리 부족 시 마지막으로 종료 |
| `Burstable` | requests < limits 또는 일부만 설정 | 중간 우선순위 |
| `BestEffort` | requests/limits 미설정 | 메모리 부족 시 가장 먼저 종료 |

**CPU Throttling vs OOMKilled 구분**

| 현상 | 원인 | 증상 | 해결 |
|------|------|------|------|
| CPU Throttling | CPU limits 너무 낮음 | 응답 지연, 처리량 감소 | limits 상향 또는 제거 |
| OOMKilled | Memory limits 너무 낮음 | 컨테이너 갑작스럽게 재시작 | limits 상향 |

---

### 2.2 실무 적용 코드

**Deployment — requests/limits 설정 예시**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-app
  namespace: production
spec:
  template:
    spec:
      containers:
        - name: app
          image: my-app:latest
          resources:
            requests:
              cpu: "250m"       # 0.25 vCPU — 스케줄링 기준
              memory: "512Mi"   # 512MB — 스케줄링 기준
            limits:
              cpu: "1"          # 최대 1 vCPU (없으면 노드 전체 사용 가능)
              memory: "1Gi"     # 최대 1GB (초과 시 OOMKilled)
              # CPU limits는 Java/Go 같은 GC 언어에서 Throttling 발생 가능
              # → CPU limits 제거하고 requests만 설정하는 것도 고려
```

**LimitRange — namespace 기본값 및 최대/최소 제한**

```yaml
apiVersion: v1
kind: LimitRange
metadata:
  name: default-limits
  namespace: production
spec:
  limits:
    - type: Container
      # Pod에 requests/limits 미설정 시 기본값 적용
      default:
        cpu: "500m"
        memory: "512Mi"
      defaultRequest:
        cpu: "100m"
        memory: "128Mi"
      # 허용 범위 제한
      max:
        cpu: "4"
        memory: "8Gi"
      min:
        cpu: "50m"
        memory: "64Mi"
```

**ResourceQuota — namespace 총 리소스 상한**

```yaml
apiVersion: v1
kind: ResourceQuota
metadata:
  name: production-quota
  namespace: production
spec:
  hard:
    # requests 총합 상한
    requests.cpu: "20"
    requests.memory: "40Gi"
    # limits 총합 상한
    limits.cpu: "40"
    limits.memory: "80Gi"
    # Pod/Service/PVC 수 제한
    pods: "50"
    services: "20"
    persistentvolumeclaims: "20"
```

**Prometheus — 실제 사용량 측정 쿼리**

```promql
# CPU 실제 사용량 p95 (Pod별)
histogram_quantile(0.95,
  rate(container_cpu_usage_seconds_total{
    namespace="production",
    container!=""
  }[5m])
)

# Memory 실제 사용량 최대값 (Pod별)
max_over_time(
  container_memory_working_set_bytes{
    namespace="production",
    container!=""
  }[7d]
) / 1024 / 1024  # MiB로 변환

# CPU Throttling 비율 (%) — 높으면 limits가 너무 낮음
rate(container_cpu_cfs_throttled_seconds_total[5m])
/ rate(container_cpu_cfs_periods_total[5m]) * 100

# OOMKilled 이벤트 감지
kube_pod_container_status_last_terminated_reason{reason="OOMKilled"} == 1
```

**VPA Off 모드로 권고값 수집**

```yaml
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata:
  name: my-app-vpa
  namespace: production
spec:
  targetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: my-app
  updatePolicy:
    updateMode: "Off"   # 권고값만 계산, 실제 변경 없음
```

```bash
# VPA 권고값 확인 (24시간 이상 수집 후)
kubectl describe vpa my-app-vpa -n production
# Recommendation:
#   Container Recommendations:
#     Container Name: app
#     Lower Bound:    cpu: 50m, memory: 256Mi
#     Target:         cpu: 250m, memory: 512Mi   ← 이 값을 requests로 설정
#     Upper Bound:    cpu: 1, memory: 2Gi
```

**적정 값 산정 기준**

```
requests = VPA Target 값 또는 Prometheus p75~p90
limits(CPU) = requests × 2~4 배 (또는 제거 고려)
limits(Memory) = requests × 1.5~2 배 (OOMKilled 방지)

Java 애플리케이션:
  memory requests = Heap 크기 + 비Heap (GC, JIT 등) + 여유
  예: -Xmx512m이면 requests 약 768Mi, limits 약 1Gi
```

---

### 2.3 보안/비용 Best Practice

- **BestEffort Pod 금지**: LimitRange로 최소 requests 강제
- **Guaranteed QoS 선호**: 중요 서비스는 requests == limits으로 Guaranteed 클래스 보장
- **CPU limits 제거 고려**: CPU는 compressible resource — Throttling이 잦다면 limits 제거하고 requests만 유지
- **Memory limits는 반드시 설정**: Memory는 non-compressible — 제한 없으면 노드 메모리를 모두 사용해 다른 Pod에 영향
- **Compute Optimizer**: AWS Compute Optimizer로 EKS Pod right-sizing 권고 확인

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**OOMKilled — Memory limits 너무 낮음**

```bash
# OOMKilled 이유로 재시작된 컨테이너 확인
kubectl get pods -n production -o json | jq \
  '.items[] | select(.status.containerStatuses[].lastState.terminated.reason=="OOMKilled") |
  {name: .metadata.name, restarts: .status.containerStatuses[].restartCount}'

# 최근 OOMKilled 이벤트
kubectl get events -n production \
  --field-selector reason=OOMKilling

# 해결: memory limits 상향
# Java: Heap 크기 제한 확인 (-Xmx), limits은 Heap + 여유 50%
```

**CPU Throttling — limits 너무 낮음**

```bash
# Throttling 비율 높은 컨테이너 확인 (Grafana 또는 직접 쿼리)
kubectl exec -it prometheus-pod -n monitoring -- \
  curl -s 'localhost:9090/api/v1/query' \
  --data-urlencode 'query=rate(container_cpu_cfs_throttled_seconds_total[5m]) / rate(container_cpu_cfs_periods_total[5m]) * 100 > 20' | \
  jq '.data.result[].metric'

# 해결: CPU limits 상향 또는 제거
# Go, Java 등 GC 언어는 GC 시 일시적으로 CPU 급증 → limits 제거 권장
```

**노드 Over-provisioning — requests가 너무 높음**

```bash
# 노드별 requests 사용률 확인
kubectl describe nodes | grep -A 10 "Allocated resources"
# CPU Requests: 7500m (93%) ← requests는 높은데
# CPU Usage:    500m  (6%)  ← 실제 사용은 낮음

# 해결: VPA 권고값 기반으로 requests 낮추기
# Compute Optimizer 권고 확인
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: requests와 limits를 같게 설정해야 하나요?**
A: 서비스 중요도에 따라 다릅니다. Guaranteed QoS(requests=limits)는 성능 예측 가능성이 높지만 노드 활용률이 낮을 수 있습니다. Burstable(requests<limits)은 유연성은 높지만 리소스 경쟁 시 성능 변동이 생깁니다.

**Q: LimitRange 설정 전에 배포된 Pod에도 적용되나요?**
A: 아니요. LimitRange는 새로 생성되는 Pod에만 적용됩니다. 기존 Pod는 재시작 후 적용됩니다.

---

## 4. 모니터링 및 알람

```yaml
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: resource-alerts
  namespace: monitoring
spec:
  groups:
    - name: resource.rules
      rules:
        - alert: ContainerOOMKilled
          expr: |
            kube_pod_container_status_last_terminated_reason{reason="OOMKilled"} == 1
          for: 0m
          labels:
            severity: warning
          annotations:
            summary: "OOMKilled ({{ $labels.namespace }}/{{ $labels.pod }}/{{ $labels.container }})"

        - alert: ContainerCPUThrottlingHigh
          expr: |
            rate(container_cpu_cfs_throttled_seconds_total[5m])
            / rate(container_cpu_cfs_periods_total[5m]) * 100 > 50
          for: 15m
          labels:
            severity: warning
          annotations:
            summary: "CPU Throttling 50% 초과 ({{ $labels.container }})"
            description: "Throttling {{ $value | humanize }}% — CPU limits 상향 검토 필요"

        - alert: NamespaceMemoryQuotaHigh
          expr: |
            kube_resourcequota{resource="requests.memory", type="used"}
            / kube_resourcequota{resource="requests.memory", type="hard"} * 100 > 80
          for: 5m
          labels:
            severity: warning
          annotations:
            summary: "네임스페이스 Memory Quota 80% 초과 ({{ $labels.namespace }})"
```

---

## 5. TIP

- **Right-sizing 프로세스**:
  1. VPA Off 모드로 7일간 권고값 수집
  2. Prometheus p90 CPU, p99 Memory 측정
  3. requests = p90~p95 값, limits = p99~max
  4. 2~4주 후 OOMKilled, Throttling 지표 모니터링
  5. 문제 없으면 다음 서비스로 반복

- **Java 힙 설정과 limits 연동**:
  ```yaml
  env:
    - name: JAVA_OPTS
      value: "-Xms256m -Xmx512m"   # Heap 최대 512m
  resources:
    requests:
      memory: "768Mi"  # Heap + 비Heap 여유
    limits:
      memory: "1Gi"    # Heap 최대 + GC 여유
  ```

- **Grafana Dashboard**: Dashboard ID `6417` (Kubernetes Resource Report) 또는 `7249` (Kubernetes Cluster)로 requests/limits/usage를 한눈에 시각화
