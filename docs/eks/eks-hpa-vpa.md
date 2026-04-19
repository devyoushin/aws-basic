# HPA & VPA — EKS Pod 오토스케일링

## 1. 개요

EKS에서 Pod 레벨 오토스케일링은 두 방향으로 동작한다.
- **HPA (Horizontal Pod Autoscaler)**: 부하에 따라 Pod 수를 늘리거나 줄임 (수평 확장)
- **VPA (Vertical Pod Autoscaler)**: Pod의 CPU/Memory requests·limits를 자동 조정 (수직 확장)

두 컴포넌트 모두 metrics-server에서 수집한 지표를 바탕으로 동작하며,
올바른 조합으로 사용해야 서비스 안정성과 비용 최적화를 동시에 달성할 수 있다.

---

## 2. 설명

### 2.1 핵심 개념

**HPA 스케일 판단 공식**

```
desiredReplicas = ceil(currentReplicas × (currentMetricValue / desiredMetricValue))
```

예: 현재 2개 Pod, CPU 사용률 140%, 목표 70%
→ `ceil(2 × (140 / 70))` = 4개로 스케일 아웃

스케일 다운은 기본 5분 안정화 대기 (flapping 방지)

**VPA 모드 4가지**

| 모드 | 동작 |
|------|------|
| `Off` | 권고값 계산만, 실제 변경 없음 (분석/튜닝 시작점) |
| `Initial` | Pod 최초 생성 시에만 requests 조정 |
| `Recreate` | requests 변경 시 Pod 재생성 (다운타임 가능) |
| `Auto` | 현재 Recreate와 동일 동작 |

**HPA + VPA 동시 사용 시 충돌 문제**

HPA가 CPU 기준으로 스케일하는 동안 VPA가 동일 컨테이너의 CPU requests를 변경하면 서로 충돌한다.

**해결책**: VPA에서 CPU를 제외하고 Memory만 관리
```yaml
resourcePolicy:
  containerPolicies:
    - containerName: app
      controlledResources: ["memory"]  # CPU는 HPA에 위임
```

**KEDA vs HPA 비교**

| 항목 | HPA | KEDA |
|------|-----|------|
| 스케일 기준 | CPU/Memory | SQS, Kafka, CloudWatch 등 이벤트 소스 |
| 0 → 1 스케일 | 불가 | 가능 (scale-to-zero) |
| 복잡도 | 낮음 | 중간 |

---

### 2.2 실무 적용 코드

**metrics-server 설치**

```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml

kubectl get deployment metrics-server -n kube-system
kubectl top nodes
kubectl top pods -A
```

**Deployment — requests 필수 설정 (HPA 동작 조건)**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-app
  namespace: production
spec:
  replicas: 2
  selector:
    matchLabels:
      app: my-app
  template:
    metadata:
      labels:
        app: my-app
    spec:
      containers:
        - name: app
          image: my-app:latest
          resources:
            requests:
              cpu: "250m"      # HPA CPU 기준값 — 반드시 설정
              memory: "256Mi"  # HPA Memory 기준값 — 반드시 설정
            limits:
              cpu: "1"
              memory: "1Gi"
```

**HPA YAML — CPU/Memory 기준**

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: my-app-hpa
  namespace: production
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: my-app
  minReplicas: 2
  maxReplicas: 20
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: 80
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 60    # 스케일 아웃 전 60초 안정화
      policies:
        - type: Percent
          value: 100                     # 한 번에 최대 100% 증가
          periodSeconds: 60
    scaleDown:
      stabilizationWindowSeconds: 300   # 스케일 인 전 5분 안정화
      policies:
        - type: Pods
          value: 2                       # 한 번에 최대 2개씩 감소
          periodSeconds: 60
```

**VPA YAML — Recreate 모드 (Memory만 관리)**

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
    updateMode: "Recreate"
  resourcePolicy:
    containerPolicies:
      - containerName: app
        controlledResources: ["memory"]   # HPA 충돌 방지
        minAllowed:
          memory: "128Mi"
        maxAllowed:
          memory: "4Gi"
        controlledValues: RequestsAndLimits
```

**VPA 설치 및 권고값 확인**

```bash
# VPA 설치
git clone https://github.com/kubernetes/autoscaler.git
cd autoscaler/vertical-pod-autoscaler
./hack/vpa-up.sh

# VPA 권고값 확인
kubectl describe vpa my-app-vpa -n production
# Recommendation:
#   Container Recommendations:
#     Container Name: app
#     Lower Bound:    memory: 128Mi
#     Target:         memory: 512Mi   ← 이 값을 requests에 반영
#     Upper Bound:    memory: 2Gi
```

**KEDA ScaledObject — SQS 기반 스케일링**

```bash
# KEDA 설치
helm repo add kedacore https://kedacore.github.io/charts
helm install keda kedacore/keda --namespace keda --create-namespace
```

```yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: sqs-worker-scaler
  namespace: production
spec:
  scaleTargetRef:
    name: my-worker
  minReplicaCount: 0    # scale-to-zero
  maxReplicaCount: 50
  pollingInterval: 30
  cooldownPeriod: 300
  triggers:
    - type: aws-sqs-queue
      authenticationRef:
        name: keda-aws-credentials
      metadata:
        queueURL: https://sqs.ap-northeast-2.amazonaws.com/123456789/my-queue
        queueLength: "10"           # Pod 1개당 처리할 메시지 수
        awsRegion: ap-northeast-2
        identityOwner: operator
---
apiVersion: keda.sh/v1alpha1
kind: TriggerAuthentication
metadata:
  name: keda-aws-credentials
  namespace: production
spec:
  podIdentity:
    provider: aws-eks   # IRSA 사용
```

---

### 2.3 보안/비용 Best Practice

- **VPA Off 모드로 먼저 권고값 수집**: 최소 24~72시간 관찰 후 requests 적용
- **KEDA scale-to-zero**: 배치 워커에 적용해 유휴 시 Pod 0개 유지
- **HPA maxReplicas 상한 설정**: 비용 폭증 방지를 위한 합리적인 상한
- **requests를 현실적으로 설정**: VPA 권고값 p75~p90 기준 적용 권장

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**HPA `<unknown>/70%` — metrics-server 미설치 또는 requests 미설정**

```bash
kubectl get hpa -n production
# TARGETS: <unknown>/70%

# 원인 1: metrics-server 미설치
kubectl get deployment metrics-server -n kube-system

# 원인 2: Deployment에 resources.requests 미설정
kubectl get deployment my-app -o jsonpath='{.spec.template.spec.containers[*].resources}'

# metrics-server 로그
kubectl logs -n kube-system deployment/metrics-server
```

**VPA 권고값이 너무 높게 설정**

```yaml
# minAllowed/maxAllowed로 상한 지정
resourcePolicy:
  containerPolicies:
    - containerName: app
      maxAllowed:
        memory: "2Gi"    # 비정상 스파이크 반영 방지
```

```bash
# VPA 이력 초기화 (잘못된 이력 제거)
kubectl delete vpa my-app-vpa -n production
kubectl apply -f vpa.yaml
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: HPA 스케일 인이 너무 느려요**
A: `stabilizationWindowSeconds`를 줄이세요.
```yaml
behavior:
  scaleDown:
    stabilizationWindowSeconds: 120   # 기본 300초 → 120초
```

**Q: KEDA가 SQS 메시지를 읽지 못해요**
A:
```bash
# KEDA 오퍼레이터 로그 확인
kubectl logs -n keda deployment/keda-operator

# IRSA 권한 확인 (sqs:GetQueueAttributes, sqs:GetQueueUrl 필요)
kubectl describe scaledobject sqs-worker-scaler -n production
```

---

## 4. 모니터링 및 알람

```yaml
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: hpa-alerts
  namespace: monitoring
spec:
  groups:
    - name: hpa.rules
      rules:
        - alert: HPAReachedMaxReplicas
          expr: |
            kube_horizontalpodautoscaler_status_current_replicas
            == kube_horizontalpodautoscaler_spec_max_replicas
          for: 10m
          labels:
            severity: warning
          annotations:
            summary: "HPA maxReplicas 도달 ({{ $labels.horizontalpodautoscaler }})"
            description: "maxReplicas 상향 또는 성능 최적화 필요"

        - alert: HPAMetricsUnavailable
          expr: |
            kube_horizontalpodautoscaler_status_condition{
              condition="ScalingActive",
              status="false"
            } == 1
          for: 5m
          labels:
            severity: critical
          annotations:
            summary: "HPA 메트릭 수집 불가 ({{ $labels.horizontalpodautoscaler }})"
```

---

## 5. TIP

- **VPA Off → Recreate 순서**: Off 모드로 며칠간 권고값 수집 → requests 수동 업데이트 → 안정화 후 Recreate 전환
- **HPA + Karpenter 연동**: HPA가 Pod를 늘릴 때 노드 자원이 부족하면 Karpenter가 자동으로 노드 추가
- **KEDA + VPA 조합 권장**: 수평 확장은 KEDA, 수직 조정은 VPA Off 모드로 권고값 수집 → 주기적으로 수동 적용 (HPA+VPA 충돌 없음)
