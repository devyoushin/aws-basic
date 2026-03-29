# EKS PersistentVolume — EBS/EFS CSI 드라이버

## 1. 개요

EKS에서 영구 스토리지를 사용하려면 CSI (Container Storage Interface) 드라이버를 통해
AWS 스토리지 서비스를 Kubernetes의 PV/PVC 체계와 연결해야 한다.
주요 드라이버는 EBS CSI Driver (단일 AZ, RWO)와 EFS CSI Driver (멀티 AZ, RWX) 두 가지다.
기존 in-tree 플러그인(`kubernetes.io/aws-ebs`)은 Kubernetes 1.27 이후 반드시 CSI 드라이버로 교체해야 한다.

---

## 2. 설명

### 2.1 핵심 개념

**PV / PVC / StorageClass 관계**

```
StorageClass (프로비저너 정의)
    ↓ 동적 프로비저닝
PV (실제 스토리지 리소스 — EBS Volume, EFS AccessPoint)
    ↑ 바인딩
PVC (Pod가 요청하는 스토리지 선언)
    ↑ 마운트
Pod (실제 사용 주체)
```

**EBS CSI Driver vs EFS CSI Driver 비교**

| 항목 | EBS CSI Driver | EFS CSI Driver |
|------|---------------|----------------|
| 스토리지 타입 | 블록 스토리지 | 파일 스토리지 (NFS) |
| 접근 모드 | RWO (ReadWriteOnce) | RWX (ReadWriteMany), ROX |
| AZ 제약 | 단일 AZ — 볼륨과 노드 동일 AZ 필수 | 멀티 AZ — 어느 AZ에서도 마운트 |
| 주요 사용처 | DB, StatefulSet | 공유 파일, 로그 수집 |
| 성능 | IOPS 설정 가능, 저지연 | 처리량 기반, 네트워크 지연 존재 |
| 동적 프로비저닝 | EBS 볼륨 신규 생성 | EFS AccessPoint 신규 생성 |
| 비용 | 프로비저닝된 용량 기준 | 사용한 용량 기준 |
| 볼륨 확장 | 지원 (allowVolumeExpansion) | 자동 확장 (EFS 특성) |

**VolumeBindingMode**
- `Immediate`: PVC 생성 즉시 EBS 볼륨 생성 (AZ 불일치 위험)
- `WaitForFirstConsumer`: Pod가 스케줄될 AZ에 맞춰 볼륨 생성 (권장)

---

### 2.2 실무 적용 코드

**Terraform — EBS CSI Driver 애드온 + IRSA**

```hcl
module "ebs_csi_irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.0"

  role_name             = "${var.cluster_name}-ebs-csi-driver"
  attach_ebs_csi_policy = true

  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["kube-system:ebs-csi-controller-sa"]
    }
  }
}

resource "aws_eks_addon" "ebs_csi_driver" {
  cluster_name             = module.eks.cluster_name
  addon_name               = "aws-ebs-csi-driver"
  addon_version            = "v1.28.0-eksbuild.1"
  service_account_role_arn = module.ebs_csi_irsa.iam_role_arn

  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"
}
```

**StorageClass — gp3 기본 설정**

```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: gp3
  annotations:
    storageclass.kubernetes.io/is-default-class: "true"
provisioner: ebs.csi.aws.com
volumeBindingMode: WaitForFirstConsumer   # AZ 불일치 방지
allowVolumeExpansion: true
parameters:
  type: gp3
  iops: "3000"
  throughput: "125"
  encrypted: "true"
reclaimPolicy: Delete
```

**StatefulSet + PVC 예시**

```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: postgres
  namespace: production
spec:
  serviceName: postgres
  replicas: 1
  selector:
    matchLabels:
      app: postgres
  template:
    metadata:
      labels:
        app: postgres
    spec:
      containers:
        - name: postgres
          image: postgres:15
          volumeMounts:
            - name: postgres-data
              mountPath: /var/lib/postgresql/data
          resources:
            requests:
              cpu: "500m"
              memory: "1Gi"
  volumeClaimTemplates:
    - metadata:
        name: postgres-data
      spec:
        accessModes:
          - ReadWriteOnce
        storageClassName: gp3
        resources:
          requests:
            storage: 50Gi
```

**EFS CSI — 동적 프로비저닝 (AccessPoint 기반)**

```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: efs-sc
provisioner: efs.csi.aws.com
parameters:
  provisioningMode: efs-ap           # AccessPoint 자동 생성
  fileSystemId: fs-xxxxxxxxxxxxxxxxx  # EFS File System ID
  directoryPerms: "700"
  gidRangeStart: "1000"
  gidRangeEnd: "2000"
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: shared-storage
  namespace: production
spec:
  accessModes:
    - ReadWriteMany    # 여러 Pod에서 동시 마운트
  storageClassName: efs-sc
  resources:
    requests:
      storage: 5Gi    # EFS는 실제 사용량 기준 과금, 여기서는 요청값만 명시
```

**VolumeSnapshot — 스냅샷 자동화**

```yaml
apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshotClass
metadata:
  name: csi-aws-vsc
  annotations:
    snapshot.storage.kubernetes.io/is-default-class: "true"
driver: ebs.csi.aws.com
deletionPolicy: Delete
---
apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshot
metadata:
  name: postgres-snapshot
  namespace: production
spec:
  volumeSnapshotClassName: csi-aws-vsc
  source:
    persistentVolumeClaimName: postgres-data-postgres-0
```

---

### 2.3 보안/비용 Best Practice

- EBS 볼륨은 KMS 암호화 필수 (`encrypted: "true"`)
- IRSA로 CSI ServiceAccount에만 IAM 권한 부여 (노드 전체 X)
- gp3 사용 (gp2 대비 동일 성능, 20% 저렴)
- 미사용 PV 주기적 정리: `kubectl get pv | grep Released`
- EFS IA (Infrequent Access) 티어 활용으로 접근 빈도 낮은 파일 비용 절감

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**PVC Pending — StorageClass 없음 또는 기본 설정 미지정**

```bash
kubectl describe pvc my-app-data
# "no persistent volumes available and no storage class configured"

# 기본 StorageClass 지정
kubectl patch storageclass gp3 \
  -p '{"metadata": {"annotations": {"storageclass.kubernetes.io/is-default-class": "true"}}}'

# gp2를 기본에서 제거 (충돌 방지)
kubectl patch storageclass gp2 \
  -p '{"metadata": {"annotations": {"storageclass.kubernetes.io/is-default-class": "false"}}}'
```

**EBS 볼륨이 다른 AZ 노드에 스케줄링 — volume node affinity conflict**

```bash
# 노드 AZ 확인
kubectl get nodes -L topology.kubernetes.io/zone

# PV nodeAffinity 확인
kubectl get pv <pv-name> -o yaml | grep -A 10 nodeAffinity

# 원인: WaitForFirstConsumer 아닌 Immediate로 생성된 PVC가
# 노드와 다른 AZ에 볼륨을 생성한 경우
# 해결: StorageClass를 WaitForFirstConsumer로 변경 후 PVC 재생성
```

**볼륨 확장 후 파일시스템 자동 반영 안 됨**

```bash
# PVC 크기 확장
kubectl patch pvc my-pvc -p '{"spec":{"resources":{"requests":{"storage":"50Gi"}}}}'

# EKS 1.24+에서는 파일시스템 자동 확장 (Pod 재시작 불필요)
# 이전 버전에서는 Pod 재시작 필요

# 확장 상태 확인
kubectl describe pvc my-pvc | grep -A 5 Conditions
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: StatefulSet 삭제 후 PVC가 남아 있어요**
A: 의도적인 Kubernetes 설계입니다. `volumeClaimTemplates`로 생성된 PVC는 StatefulSet 삭제 시 자동 삭제되지 않습니다.
```bash
kubectl delete pvc -l app=postgres -n production
```

**Q: ReclaimPolicy를 Retain으로 변경하고 싶어요**
A:
```bash
kubectl patch pv <pv-name> -p '{"spec":{"persistentVolumeReclaimPolicy":"Retain"}}'
```

---

## 4. 모니터링 및 알람

```yaml
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: pvc-alerts
  namespace: monitoring
spec:
  groups:
    - name: pvc.rules
      rules:
        - alert: PVCUsageHigh
          expr: |
            (kubelet_volume_stats_used_bytes / kubelet_volume_stats_capacity_bytes) * 100 > 80
          for: 5m
          labels:
            severity: warning
          annotations:
            summary: "PVC 사용률 높음 ({{ $labels.namespace }}/{{ $labels.persistentvolumeclaim }})"
            description: "사용률 {{ $value | humanize }}%"

        - alert: PVCPendingTooLong
          expr: kube_persistentvolumeclaim_status_phase{phase="Pending"} == 1
          for: 10m
          labels:
            severity: warning
          annotations:
            summary: "PVC Pending 10분 초과 ({{ $labels.persistentvolumeclaim }})"
```

**주요 PromQL**

```promql
# PVC 사용률 (%)
(kubelet_volume_stats_used_bytes / kubelet_volume_stats_capacity_bytes) * 100

# PVC 남은 용량 (GiB)
(kubelet_volume_stats_capacity_bytes - kubelet_volume_stats_used_bytes) / 1073741824

# inode 사용률 (%)
(kubelet_volume_stats_inodes_used / kubelet_volume_stats_inodes) * 100
```

---

## 5. TIP

- **미사용 EBS 볼륨 탐지**: `kubectl get pv | grep Released` 및 AWS 콘솔에서 `available` 상태 볼륨 정기 정리
- **Multi-AZ StatefulSet**: EBS는 단일 AZ 제약으로 불가 → EFS 또는 Rook-Ceph 사용 권장
- Grafana Dashboard ID `13646` (Kubernetes Persistent Volumes)으로 PVC 사용률 시각화 가능
