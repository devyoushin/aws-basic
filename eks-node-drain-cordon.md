# 노드 드레인/코든 & PodDisruptionBudget (EKS)

## 1. 개요

EKS 클러스터 운영 중 노드 교체, 업그레이드, 스케일 인 작업 시 실행 중인 Pod를 안전하게 이동시키는 절차가 필요하다.
- **cordon (코든)**: 노드에 새 Pod 스케줄 차단 (`SchedulingDisabled` 상태)
- **drain (드레인)**: 기존 Pod를 모두 퇴거(Evict)시키고 cordon 적용
- **PodDisruptionBudget (PDB)**: drain 중 서비스 가용성을 보장하는 정책

---

## 2. 설명

### 2.1 핵심 개념

**cordon vs drain 차이**

| 항목 | cordon | drain |
|------|--------|-------|
| 동작 | 신규 Pod 스케줄 차단 | 기존 Pod 퇴거 + cordon |
| 기존 Pod | 영향 없음 | Eviction API로 종료 요청 |
| PDB 체크 | 없음 | 있음 (PDB 위반 시 중단) |
| 사용 시점 | 점검 준비 | 노드 교체, 업그레이드 전 |

**drain 동작 원리**

```
kubectl drain node-1
    │
    ├─ 1. cordon: NoSchedule taint 추가
    ├─ 2. Pod 목록 조회 (DaemonSet, static Pod 제외)
    ├─ 3. 각 Pod에 Eviction API 요청
    │        ├─ PDB 위반 없으면 → Pod 종료
    │        └─ PDB 위반이면 → 429 응답, 재시도 대기
    ├─ 4. terminationGracePeriodSeconds 대기
    │        ├─ preStop hook 실행
    │        └─ SIGTERM → (grace period) → SIGKILL
    └─ 5. 모든 Pod 퇴거 완료 → drain 성공
```

**PodDisruptionBudget (PDB) 필드**

| 필드 | 설명 | 예시 |
|------|------|------|
| `minAvailable` | 항상 유지해야 할 최소 Pod 수/비율 | `2` 또는 `"50%"` |
| `maxUnavailable` | 동시에 중단 가능한 최대 Pod 수/비율 | `1` 또는 `"25%"` |

주의: replicas가 1인 Deployment에 `minAvailable: 1` PDB를 설정하면 drain이 영구적으로 차단된다.

---

### 2.2 실무 적용 코드

**cordon / drain / uncordon 기본 명령어**

```bash
# 노드 cordon (신규 스케줄 차단)
kubectl cordon ip-10-0-1-100.ap-northeast-2.compute.internal

# 노드 상태 확인 (STATUS: Ready,SchedulingDisabled)
kubectl get nodes

# 노드 drain
kubectl drain ip-10-0-1-100.ap-northeast-2.compute.internal \
  --ignore-daemonsets \         # DaemonSet Pod 건너뜀
  --delete-emptydir-data \      # emptyDir 데이터 삭제 허용
  --timeout=300s \              # 300초 내 완료 안 되면 실패
  --grace-period=60             # 각 Pod에 60초 grace period

# 작업 완료 후 uncordon
kubectl uncordon ip-10-0-1-100.ap-northeast-2.compute.internal

# drain 전 노드의 Pod 목록 확인
kubectl get pods -A -o wide \
  --field-selector spec.nodeName=ip-10-0-1-100.ap-northeast-2.compute.internal
```

**PDB YAML 예시**

```yaml
# 최소 2개 유지
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: my-app-pdb
  namespace: production
spec:
  minAvailable: 2
  selector:
    matchLabels:
      app: my-app
---
# 최대 1개 동시 중단 허용 (롤링 업데이트에 적합)
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: my-app-pdb-max
  namespace: production
spec:
  maxUnavailable: 1
  selector:
    matchLabels:
      app: my-app
```

**terminationGracePeriodSeconds + preStop hook**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-app
  namespace: production
spec:
  template:
    spec:
      terminationGracePeriodSeconds: 90  # preStop(15s) + 앱 종료(60s) + 여유(15s)
      containers:
        - name: app
          image: my-app:latest
          lifecycle:
            preStop:
              exec:
                # NLB deregistration delay 확보 (로드밸런서가 새 요청 차단 대기)
                command: ["/bin/sh", "-c", "sleep 15"]
```

**PDB 상태 확인**

```bash
# PDB 목록 및 ALLOWED DISRUPTIONS 확인
kubectl get pdb -A

# 상세 확인
kubectl describe pdb my-app-pdb -n production
# ALLOWED DISRUPTIONS: 1 → 현재 1개 Pod 중단 가능
# ALLOWED DISRUPTIONS: 0 → drain 불가 상태
```

**관리형 노드그룹 업데이트 시 자동 drain**

```bash
# 노드그룹 AMI 버전 업데이트 (자동 drain 포함)
aws eks update-nodegroup-version \
  --cluster-name my-cluster \
  --nodegroup-name my-nodegroup \
  --release-version 1.29.x-20240101

# 업데이트 진행 상황 확인
aws eks describe-update \
  --cluster-name my-cluster \
  --nodegroup-name my-nodegroup \
  --update-id <update-id>
```

주의: PDB로 drain이 차단되면 15분 타임아웃 후 업데이트가 실패할 수 있다. 업데이트 전 `ALLOWED DISRUPTIONS >= 1` 확인 필수.

**여러 노드 순차 drain 스크립트**

```bash
#!/bin/bash
NODES=$(kubectl get nodes -l nodegroup=my-nodegroup -o name)

for NODE in $NODES; do
  echo "Draining $NODE..."
  kubectl drain $NODE \
    --ignore-daemonsets \
    --delete-emptydir-data \
    --timeout=300s

  if [ $? -eq 0 ]; then
    echo "$NODE drained successfully"
    sleep 30   # 다음 노드 전 서비스 안정화 대기
  else
    echo "FAILED: $NODE — stopping"
    exit 1
  fi
done
```

---

### 2.3 보안/비용 Best Practice

- 모든 프로덕션 Deployment에 PDB 필수 적용
- `maxUnavailable: 1`이 대부분의 경우 가장 안전한 설정
- `replicas: 1` 서비스는 drain 시 다운타임 불가피 → 최소 `replicas: 2`로 설계
- Karpenter Consolidation 중 특정 Pod 보호:
  ```yaml
  metadata:
    annotations:
      karpenter.sh/do-not-disrupt: "true"
  ```

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**drain stuck — PDB 차단**

```bash
# 증상
# error: Cannot evict pod as it would violate the pod's disruption budget.

# ALLOWED DISRUPTIONS 확인
kubectl get pdb -n production

# 방법 1: 다른 노드에 먼저 Pod 추가해 ALLOWED DISRUPTIONS 확보
kubectl scale deployment my-app --replicas=4 -n production

# 방법 2: PDB 임시 삭제 (서비스 중단 위험 있음 — 비권장)
kubectl delete pdb my-app-pdb -n production
kubectl drain <node> --ignore-daemonsets --delete-emptydir-data
kubectl apply -f pdb.yaml   # 즉시 복원
```

**drain stuck — DaemonSet Pod**

```bash
# 해결: --ignore-daemonsets 플래그 추가
kubectl drain <node> --ignore-daemonsets --delete-emptydir-data
```

**drain stuck — emptyDir 볼륨**

```bash
# 해결: --delete-emptydir-data 플래그 추가 (데이터 영구 삭제 주의)
kubectl drain <node> --ignore-daemonsets --delete-emptydir-data
```

**force drain의 위험성**

```bash
# 절대 권장하지 않음
kubectl drain <node> --force --grace-period=0
# --force: PDB 무시, 즉시 삭제 → 서비스 중단 가능
# --grace-period=0: SIGTERM 없이 SIGKILL → DB 연결 강제 종료 위험
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: drain 중 Pod가 Pending으로 남아요**
A:
```bash
kubectl describe pod <pod> | grep -A 10 Events
# 일반 원인:
# 1. 모든 노드 cordon 상태 → 일부 uncordon
# 2. 노드 자원 부족 → Karpenter/CA 노드 추가 대기
# 3. nodeAffinity/toleration 불일치
```

**Q: PDB 없는 네임스페이스를 탐지하고 싶어요**
A:
```bash
for NS in $(kubectl get ns -o name | cut -d/ -f2); do
  DEPLOY=$(kubectl get deploy -n $NS --no-headers 2>/dev/null | wc -l)
  PDB=$(kubectl get pdb -n $NS --no-headers 2>/dev/null | wc -l)
  if [ "$DEPLOY" -gt 0 ] && [ "$PDB" -eq 0 ]; then
    echo "WARNING: $NS has $DEPLOY deployments but no PDB"
  fi
done
```

---

## 4. 모니터링 및 알람

```yaml
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: node-drain-alerts
  namespace: monitoring
spec:
  groups:
    - name: node.rules
      rules:
        - alert: NodeNotReady
          expr: kube_node_status_condition{condition="Ready",status="true"} == 0
          for: 5m
          labels:
            severity: critical
          annotations:
            summary: "노드 NotReady ({{ $labels.node }})"

        - alert: NodeSchedulingDisabledTooLong
          expr: kube_node_spec_unschedulable == 1
          for: 30m
          labels:
            severity: warning
          annotations:
            summary: "노드 SchedulingDisabled 30분 초과 ({{ $labels.node }})"

        - alert: PDBNoDisruptionsAllowed
          expr: kube_poddisruptionbudget_status_pod_disruptions_allowed == 0
          for: 60m
          labels:
            severity: warning
          annotations:
            summary: "PDB ALLOWED DISRUPTIONS 0 ({{ $labels.poddisruptionbudget }})"
            description: "노드 업데이트가 차단될 수 있습니다"
```

---

## 5. TIP

**배포 파이프라인 PDB 체크리스트**

```
[ ] Deployment replicas >= 2
[ ] PDB 생성 여부 확인 (kubectl get pdb -n <namespace>)
[ ] PDB selector가 Deployment labels와 일치하는지 확인
[ ] ALLOWED DISRUPTIONS >= 1 확인
[ ] terminationGracePeriodSeconds >= 실제 앱 종료 시간
[ ] preStop hook으로 로드밸런서 연결 drain 대기 (최소 10~15초)
[ ] 관리형 노드그룹 업데이트 전 PDB 상태 재확인
```

- **Karpenter Consolidation**: PDB를 준수하므로 ALLOWED DISRUPTIONS가 0이면 자동 대기 후 재시도
