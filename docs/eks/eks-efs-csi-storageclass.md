# EKS EFS CSI Driver와 StorageClass 운영

## 1. 개요

EKS에서 여러 Pod가 같은 파일시스템을 동시에 읽고 써야 한다면 EFS CSI Driver를 사용한다.
EBS는 단일 AZ와 `ReadWriteOnce` 중심이라 StatefulSet의 디스크에는 적합하지만, 여러 노드/Pod가 공유해야 하는 업로드 파일, 리포트 산출물, ML 학습 데이터, 공통 설정 파일에는 EFS가 더 맞다.

핵심 구성은 아래 순서로 잡는다.

```text
1. EFS File System 생성
2. 각 AZ private subnet에 Mount Target 생성
3. EKS 노드 또는 Pod ENI에서 EFS Security Group 2049 접근 허용
4. EFS CSI Driver 설치
5. StorageClass 생성
6. PVC 생성
7. Pod에 volumeMount 적용
8. mount, 권한, 성능, 비용 지표 확인
```

EFS를 EKS에서 쓸 때 가장 중요한 판단은 `동적 프로비저닝`과 `정적 프로비저닝` 중 무엇을 쓸지다.

| 방식 | 설명 | 적합한 경우 |
|------|------|-------------|
| 동적 프로비저닝 | PVC 생성 시 EFS Access Point 자동 생성 | 팀/서비스가 PVC를 자주 만들고 자동화가 중요한 경우 |
| 정적 프로비저닝 | Terraform 등으로 EFS Access Point를 미리 만들고 PV에 연결 | AP 경로, UID/GID, 생명주기를 IaC로 강하게 통제할 경우 |

---

## 2. 사전 조건

### EFS 네트워크 조건

EFS는 NFS 2049 포트를 사용한다. EKS 노드가 있는 각 AZ에서 EFS Mount Target이 있어야 AZ 간 불필요한 네트워크 경로와 장애 위험을 줄일 수 있다.

```text
EKS Node Subnet ap-northeast-2a -> EFS Mount Target ap-northeast-2a
EKS Node Subnet ap-northeast-2c -> EFS Mount Target ap-northeast-2c
```

Security Group 기준:

| 방향 | Source | Destination | Port |
|------|--------|-------------|------|
| ingress | EKS node SG 또는 Pod SG | EFS SG | TCP 2049 |
| egress | EKS node SG 또는 Pod SG | EFS SG | TCP 2049 |

Security Groups for Pods를 쓰는 환경이면 노드 SG가 아니라 Pod ENI에 붙는 SG에서 EFS SG로 2049가 열려 있어야 한다.

### CSI Driver 권한

동적 프로비저닝으로 Access Point를 자동 생성하려면 EFS CSI Controller가 EFS API를 호출할 권한이 필요하다.
권한은 노드 IAM Role에 몰아주지 말고 IRSA 또는 EKS Pod Identity로 CSI ServiceAccount에만 부여한다.

---

## 3. EFS와 Mount Target 생성

Terraform 예시:

```hcl
resource "aws_efs_file_system" "shared" {
  creation_token   = "${var.cluster_name}-shared-efs"
  performance_mode = "generalPurpose"
  throughput_mode  = "elastic"
  encrypted        = true
  kms_key_id       = aws_kms_key.efs.arn

  lifecycle_policy {
    transition_to_ia = "AFTER_30_DAYS"
  }

  tags = {
    Name        = "${var.cluster_name}-shared-efs"
    Environment = var.environment
  }
}

resource "aws_security_group" "efs" {
  name   = "${var.cluster_name}-efs-sg"
  vpc_id = var.vpc_id

  ingress {
    description     = "NFS from EKS nodes"
    from_port       = 2049
    to_port         = 2049
    protocol        = "tcp"
    security_groups = [var.eks_node_security_group_id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_efs_mount_target" "az" {
  for_each = toset(var.private_subnet_ids)

  file_system_id  = aws_efs_file_system.shared.id
  subnet_id       = each.value
  security_groups = [aws_security_group.efs.id]
}
```

운영 기준:

- EKS 워커 노드가 배치되는 AZ마다 Mount Target을 둔다.
- EFS SG ingress는 가능하면 EKS node SG 또는 Pod SG로 제한한다.
- cross-VPC, cross-account 마운트가 필요하면 DNS, routing, SG, IAM 조건을 별도로 검토한다.

---

## 4. EFS CSI Driver 설치

EKS add-on으로 관리하는 방식이 운영상 가장 단순하다.

```hcl
module "efs_csi_irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.0"

  role_name             = "${var.cluster_name}-efs-csi-driver"
  attach_efs_csi_policy = true

  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["kube-system:efs-csi-controller-sa"]
    }
  }
}

resource "aws_eks_addon" "efs_csi_driver" {
  cluster_name             = module.eks.cluster_name
  addon_name               = "aws-efs-csi-driver"
  service_account_role_arn = module.efs_csi_irsa.iam_role_arn

  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"
}
```

확인:

```bash
kubectl get pods -n kube-system -l app.kubernetes.io/name=aws-efs-csi-driver
kubectl get csidriver efs.csi.aws.com
kubectl describe sa efs-csi-controller-sa -n kube-system
```

---

## 5. 동적 프로비저닝 StorageClass

동적 프로비저닝은 PVC마다 EFS Access Point를 자동 생성한다.
서비스/팀 단위로 PVC를 자주 만들거나 Kubernetes manifest 중심으로 운영할 때 적합하다.

```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: efs-ap
provisioner: efs.csi.aws.com
parameters:
  provisioningMode: efs-ap
  fileSystemId: fs-xxxxxxxxxxxxxxxxx
  directoryPerms: "750"
  gidRangeStart: "10000"
  gidRangeEnd: "19999"
  basePath: "/k8s"
reclaimPolicy: Delete
volumeBindingMode: Immediate
```

중요 파라미터:

| 파라미터 | 의미 | 운영 기준 |
|----------|------|-----------|
| `provisioningMode: efs-ap` | PVC 생성 시 Access Point 자동 생성 | 동적 프로비저닝 필수 |
| `fileSystemId` | 연결할 EFS 파일시스템 ID | 환경별로 명확히 분리 |
| `directoryPerms` | AP 루트 경로 권한 | 보통 `750` 또는 `770` |
| `gidRangeStart/End` | 자동 할당할 GID 범위 | 팀/환경별 충돌 방지 |
| `basePath` | AP 경로가 생성될 상위 디렉터리 | `/k8s`, `/prod`, `/team-a` 등 |
| `reclaimPolicy` | PVC 삭제 시 PV/AP 처리 | 실습은 Delete, 운영 데이터는 신중히 선택 |

PVC 예시:

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: uploads
  namespace: production
spec:
  accessModes:
    - ReadWriteMany
  storageClassName: efs-ap
  resources:
    requests:
      storage: 10Gi
```

Deployment 예시:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: upload-api
  namespace: production
spec:
  replicas: 3
  selector:
    matchLabels:
      app: upload-api
  template:
    metadata:
      labels:
        app: upload-api
    spec:
      securityContext:
        fsGroup: 10000
      containers:
        - name: app
          image: public.ecr.aws/nginx/nginx:latest
          volumeMounts:
            - name: uploads
              mountPath: /data/uploads
      volumes:
        - name: uploads
          persistentVolumeClaim:
            claimName: uploads
```

확인:

```bash
kubectl get sc efs-ap
kubectl get pvc -n production uploads
kubectl get pv
kubectl describe pvc -n production uploads
aws efs describe-access-points --file-system-id fs-xxxxxxxxxxxxxxxxx
```

---

## 6. 정적 프로비저닝 StorageClass + PV

Access Point를 Terraform으로 먼저 만들고 Kubernetes PV에 직접 연결하면 경로와 UID/GID를 강하게 통제할 수 있다.
운영 데이터, 팀별 격리, 감사 요구사항이 있는 경우 이 방식이 더 예측 가능하다.

Terraform Access Point 예시:

```hcl
resource "aws_efs_access_point" "team_a" {
  file_system_id = aws_efs_file_system.shared.id

  posix_user {
    uid = 10001
    gid = 10001
  }

  root_directory {
    path = "/teams/team-a/uploads"

    creation_info {
      owner_uid   = 10001
      owner_gid   = 10001
      permissions = "750"
    }
  }

  tags = {
    Name = "${var.cluster_name}-team-a-uploads"
    Team = "team-a"
  }
}
```

Kubernetes 예시:

```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: efs-team-a
provisioner: efs.csi.aws.com
reclaimPolicy: Retain
volumeBindingMode: Immediate
---
apiVersion: v1
kind: PersistentVolume
metadata:
  name: efs-team-a-uploads
spec:
  capacity:
    storage: 10Gi
  volumeMode: Filesystem
  accessModes:
    - ReadWriteMany
  persistentVolumeReclaimPolicy: Retain
  storageClassName: efs-team-a
  csi:
    driver: efs.csi.aws.com
    volumeHandle: fs-xxxxxxxxxxxxxxxxx::fsap-xxxxxxxxxxxxxxxxx
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: uploads
  namespace: team-a
spec:
  accessModes:
    - ReadWriteMany
  storageClassName: efs-team-a
  volumeName: efs-team-a-uploads
  resources:
    requests:
      storage: 10Gi
```

정적 프로비저닝 운영 기준:

- `persistentVolumeReclaimPolicy: Retain`으로 데이터 삭제를 방지한다.
- PV와 PVC 이름에 팀/서비스/용도를 넣는다.
- `volumeHandle`은 `fileSystemId::accessPointId` 형식으로 작성한다.
- AP 삭제는 PVC/PV 삭제와 분리해서 IaC 변경 리뷰를 거치게 한다.

---

## 7. 어떤 StorageClass를 기본으로 둘 것인가

EFS를 default StorageClass로 지정하는 것은 보통 권장하지 않는다.
EKS에서 일반적인 기본값은 EBS gp3이고, EFS는 명시적으로 필요한 워크로드에서만 `storageClassName`으로 지정하는 편이 안전하다.

```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: gp3
  annotations:
    storageclass.kubernetes.io/is-default-class: "true"
provisioner: ebs.csi.aws.com
volumeBindingMode: WaitForFirstConsumer
allowVolumeExpansion: true
parameters:
  type: gp3
  encrypted: "true"
reclaimPolicy: Delete
```

EFS StorageClass는 명시적으로 사용한다.

```yaml
spec:
  storageClassName: efs-ap
```

판단 기준:

| 워크로드 | 권장 스토리지 |
|----------|---------------|
| 단일 Pod DB, 낮은 지연시간 필요 | EBS gp3 |
| 여러 Pod 공유 업로드 디렉터리 | EFS |
| 여러 AZ에서 동시에 읽는 공통 파일 | EFS |
| 높은 IOPS DB | EBS io2/gp3 튜닝 |
| 정적 파일 배포 | S3 + CloudFront 우선 검토 |

---

## 8. 권한과 보안 운영

EFS는 POSIX 권한과 AWS 네트워크/IAM 권한이 같이 맞아야 한다.
마운트가 성공해도 UID/GID가 맞지 않으면 Pod 내부에서 파일 생성이 실패할 수 있다.

Pod 보안 기준:

```yaml
spec:
  securityContext:
    runAsUser: 10001
    runAsGroup: 10001
    fsGroup: 10001
```

운영 체크:

```bash
kubectl exec -n team-a deploy/upload-api -- id
kubectl exec -n team-a deploy/upload-api -- sh -c 'touch /data/uploads/test && ls -l /data/uploads/test'
kubectl exec -n team-a deploy/upload-api -- df -h /data/uploads
```

보안 기준:

- EFS SG는 2049를 전체 VPC CIDR로 열기보다 EKS node SG 또는 Pod SG로 제한한다.
- EFS는 암호화를 켜고 KMS key를 명시한다.
- Access Point별 UID/GID를 팀/서비스 단위로 분리한다.
- 직접 EFS root를 마운트하는 방식은 멀티테넌트 환경에서 피한다.

---

## 9. 성능과 비용 기준

EFS는 네트워크 파일시스템이다.
로컬 디스크나 EBS처럼 낮은 지연시간을 기대하면 안 된다.

성능 기준:

- 작은 파일을 매우 많이 생성/삭제하는 워크로드는 느릴 수 있다.
- latency가 민감한 DB 데이터 디렉터리에는 적합하지 않다.
- 여러 Pod가 같은 파일에 동시에 쓰는 경우 애플리케이션 레벨 locking을 검토한다.
- 처리량이 출렁이는 워크로드는 Elastic Throughput을 우선 검토한다.

비용 기준:

- 사용량 기반 과금이라 PVC `requests.storage` 값이 곧 비용은 아니다.
- IA 전환 정책을 켜면 오래 안 쓰는 파일 비용을 줄일 수 있다.
- IA 파일을 자주 다시 읽는 패턴이면 오히려 비용이 늘 수 있다.
- CloudWatch `BurstCreditBalance`, `PercentIOLimit`, `ClientConnections`, `DataReadIOBytes`, `DataWriteIOBytes`를 확인한다.

---

## 10. 트러블슈팅

### PVC가 Pending

확인:

```bash
kubectl describe pvc -n production uploads
kubectl get events -n production --sort-by=.lastTimestamp
kubectl logs -n kube-system -l app=efs-csi-controller -c efs-plugin
```

주요 원인:

- EFS CSI Driver 미설치
- StorageClass의 `fileSystemId` 오타
- EFS CSI Controller IRSA 권한 부족
- `gidRangeStart/End` 범위 소진

### Pod가 ContainerCreating에서 멈춤

확인:

```bash
kubectl describe pod -n production <pod-name>
kubectl logs -n kube-system -l app=efs-csi-node -c efs-plugin
```

주요 원인:

- EFS SG 2049 ingress 미허용
- 노드 subnet의 AZ에 Mount Target 없음
- DNS 해석 실패
- Security Groups for Pods 환경에서 Pod SG 기준 허용 누락

### Permission denied

확인:

```bash
kubectl exec -n production <pod-name> -- id
kubectl exec -n production <pod-name> -- ls -ld /data/uploads
```

주요 원인:

- Access Point UID/GID와 Pod `runAsUser`, `fsGroup` 불일치
- `directoryPerms`가 너무 제한적
- 기존 경로가 다른 UID/GID로 이미 생성됨

### 삭제했는데 데이터가 남음

EFS는 파일시스템이고, PVC 삭제가 항상 실제 파일 삭제를 의미하지 않는다.
특히 정적 PV에서 `Retain`을 쓰면 PV/PVC를 삭제해도 EFS 데이터와 Access Point는 남는다.

확인:

```bash
kubectl get pv
aws efs describe-access-points --file-system-id fs-xxxxxxxxxxxxxxxxx
aws efs describe-file-systems --file-system-id fs-xxxxxxxxxxxxxxxxx
```

운영에서는 데이터 삭제를 Kubernetes PVC 삭제에만 맡기지 말고, EFS/AP 삭제 절차를 별도 runbook으로 둔다.

---

## 11. 운영 체크리스트

```text
[ ] EKS 노드가 있는 모든 AZ에 EFS Mount Target 생성
[ ] EFS SG 2049 ingress를 EKS node SG 또는 Pod SG로 제한
[ ] EFS CSI Driver 설치 확인
[ ] CSI Controller 권한은 IRSA 또는 Pod Identity로 분리
[ ] EFS는 default StorageClass로 지정하지 않음
[ ] EBS gp3를 default로 두고 EFS는 storageClassName으로 명시
[ ] 동적/정적 프로비저닝 기준 확정
[ ] Access Point UID/GID와 Pod securityContext 정합성 확인
[ ] 운영 데이터는 Retain 정책 검토
[ ] EFS CloudWatch 지표와 비용 지표 확인
[ ] PVC Pending, mount 실패, Permission denied 대응 절차 준비
```

관련 문서:

- [EKS PersistentVolume — EBS/EFS CSI 드라이버](eks-persistent-volume.md)
- [EFS Access Point](../storage/efs-access-point.md)
- [EKS IRSA](eks-irsa.md)
