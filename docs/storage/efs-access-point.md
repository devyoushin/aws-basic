# EFS Access Point

## 1. 개요

EFS Access Point (액세스 포인트)는 Amazon EFS 파일시스템에 대한 애플리케이션별 진입점으로,
각 접속 주체에게 **고정된 POSIX UID/GID**와 **격리된 루트 경로**를 강제 적용한다.
하나의 EFS 파일시스템을 여러 팀/서비스가 공유하면서도 서로의 데이터에 접근할 수 없도록
격리하는 멀티테넌트 스토리지 패턴의 핵심 구성 요소다.

운영 측면에서는 EKS CSI 드라이버의 동적 프로비저닝, 크로스 계정 공유, Fargate Pod 스토리지,
Lambda 함수의 영구 스토리지 마운트 등 다양한 시나리오에서 활용된다.

---

## 2. 설명

### 2.1 핵심 개념

**EFS Access Point 동작 원리**

```
클라이언트 (Pod / Lambda / EC2)
        │
        │ NFS 마운트 (포트 2049)
        ▼
┌─────────────────────────────────┐
│       EFS Access Point          │
│  ① 루트 경로 제한 (chroot 효과) │
│  ② POSIX UID/GID 강제 주입     │
│  ③ 디렉터리 자동 생성          │
└─────────────────────────────────┘
        │
        ▼
EFS File System (실제 데이터)
  /team-a/data  ← AP-A만 볼 수 있음
  /team-b/data  ← AP-B만 볼 수 있음
  /shared/logs  ← AP-C만 볼 수 있음
```

**세 가지 핵심 기능**

| 기능 | 설명 | 효과 |
|------|------|------|
| **루트 경로 격리** | Access Point가 가리키는 경로가 클라이언트의 `/`로 보임 | 다른 경로 접근 물리적 차단 |
| **POSIX UID/GID 강제 주입** | 접속 클라이언트의 UID/GID를 무시하고 AP에 설정된 값으로 덮어씀 | root 탈출 방지, 권한 일관성 보장 |
| **디렉터리 자동 생성** | `root_directory.creation_info`로 루트 경로가 없으면 자동 생성 | Terraform 배포 시 수동 초기화 불필요 |

**Access Point vs EFS 직접 마운트 비교**

| 항목 | EFS 직접 마운트 | Access Point 마운트 |
|------|---------------|-------------------|
| 보이는 경로 | 파일시스템 전체 (`/`) | AP 루트 경로만 (`/team-a/data` → `/`) |
| UID/GID 결정 | 클라이언트 프로세스 권한 | AP에 설정된 값으로 강제 오버라이드 |
| 멀티테넌트 격리 | 어렵고 실수하기 쉬움 | 구조적으로 격리 보장 |
| EKS 동적 프로비저닝 | 불가 (CSI 드라이버 요구사항) | 가능 (`provisioningMode: efs-ap`) |
| IAM 조건부 접근 제어 | 불가 | `elasticfilesystem:AccessPointArn` 조건으로 가능 |

**EFS 파일시스템 구조 예시**

```
EFS File System (fs-xxxxxxxxxxxxxxxxx)
│
├── / (EFS 루트 — 직접 마운트 시에만 보임)
│   ├── /team-a/data        ← Access Point A (AP-a: uid=1000, gid=1000)
│   │     └── uploads/
│   │     └── processed/
│   ├── /team-b/data        ← Access Point B (AP-b: uid=2000, gid=2000)
│   ├── /app/uploads        ← Access Point C (AP-c: uid=1500, gid=1500)
│   └── /shared/configs     ← Access Point D (AP-d: 읽기 전용 공유)
│
→ AP-a로 마운트한 Pod는 /team-b/data, /shared/configs에 접근 불가
```

---

### 2.2 실무 적용 코드

#### EFS 파일시스템 + Security Group + 마운트 타겟 (Terraform)

```hcl
# ── EFS 파일시스템 ────────────────────────────────────────────────
resource "aws_efs_file_system" "main" {
  creation_token   = "${var.cluster_name}-efs"
  performance_mode = "generalPurpose"   # maxIO는 수천 개 동시 연결 시
  throughput_mode  = "elastic"          # 워크로드에 따라 자동 조정 (권장)
  encrypted        = true
  kms_key_id       = aws_kms_key.efs.arn

  # IA 티어로 자동 이동 (30일 미접근 시)
  lifecycle_policy {
    transition_to_ia                    = "AFTER_30_DAYS"
    transition_to_primary_storage_class = "AFTER_1_ACCESS"  # 재접근 시 Standard로 복귀
  }

  tags = {
    Name        = "${var.cluster_name}-efs"
    Environment = var.environment
  }
}

# ── Security Group ─────────────────────────────────────────────────
resource "aws_security_group" "efs" {
  name        = "${var.cluster_name}-efs-sg"
  description = "EFS NFS access from EKS nodes"
  vpc_id      = var.vpc_id

  ingress {
    description     = "NFS from EKS nodes"
    from_port       = 2049
    to_port         = 2049
    protocol        = "tcp"
    security_groups = [var.eks_node_sg_id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.cluster_name}-efs-sg" }
}

# ── 마운트 타겟 (각 AZ 서브넷마다 생성) ──────────────────────────
resource "aws_efs_mount_target" "az" {
  for_each = toset(var.private_subnet_ids)

  file_system_id  = aws_efs_file_system.main.id
  subnet_id       = each.value
  security_groups = [aws_security_group.efs.id]
}
```

#### Access Point 생성 (Terraform)

```hcl
# ── Access Point 정의 ──────────────────────────────────────────────
locals {
  efs_access_points = {
    "team-a" = {
      path        = "/teams/team-a"
      uid         = 1000
      gid         = 1000
      permissions = "750"
    }
    "team-b" = {
      path        = "/teams/team-b"
      uid         = 2000
      gid         = 2000
      permissions = "750"
    }
    "app-uploads" = {
      path        = "/apps/uploads"
      uid         = 1500
      gid         = 1500
      permissions = "770"
    }
    "shared-configs" = {
      path        = "/shared/configs"
      uid         = 0     # root 소유 (읽기 전용 목적)
      gid         = 0
      permissions = "555"
    }
  }
}

resource "aws_efs_access_point" "main" {
  for_each = local.efs_access_points

  file_system_id = aws_efs_file_system.main.id

  # 클라이언트의 UID/GID를 이 값으로 강제 오버라이드
  posix_user {
    uid            = each.value.uid
    gid            = each.value.gid
    secondary_gids = []   # 추가 보조 GID (필요 시 지정)
  }

  # AP가 노출하는 루트 경로 (클라이언트에게 /로 보임)
  root_directory {
    path = each.value.path

    # 경로가 없으면 아래 설정으로 자동 생성
    creation_info {
      owner_uid   = each.value.uid
      owner_gid   = each.value.gid
      permissions = each.value.permissions
    }
  }

  tags = {
    Name        = "${var.cluster_name}-ap-${each.key}"
    Team        = each.key
    Environment = var.environment
  }
}

# ── 출력 (EKS StorageClass / PV volumeHandle에 사용) ─────────────
output "efs_file_system_id" {
  value = aws_efs_file_system.main.id
}

output "efs_access_point_ids" {
  value = {
    for k, v in aws_efs_access_point.main : k => v.id
  }
  # 예: { "team-a" = "fsap-0abc123...", "team-b" = "fsap-0def456..." }
}
```

#### EFS CSI 드라이버 설치 + IRSA (Terraform)

```hcl
module "efs_csi_irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.0"

  role_name             = "${var.cluster_name}-efs-csi-driver"
  attach_efs_csi_policy = true  # AmazonEFSCSIDriverPolicy 자동 연결

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
  addon_version            = "v2.0.7-eksbuild.1"
  service_account_role_arn = module.efs_csi_irsa.iam_role_arn

  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "PRESERVE"
}
```

#### EKS — 정적 프로비저닝 (Terraform으로 생성한 AP 재사용)

Terraform에서 관리하는 Access Point를 Kubernetes PV에 직접 연결하는 방식.
AP 수가 고정되고 Terraform으로 라이프사이클을 통합 관리할 때 적합하다.

```yaml
# ── team-a 전용 StorageClass ───────────────────────────────────────
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: efs-team-a
provisioner: efs.csi.aws.com
reclaimPolicy: Retain        # PVC 삭제 시 PV(AP)를 보존
volumeBindingMode: Immediate
---
# ── team-a 전용 PersistentVolume ──────────────────────────────────
apiVersion: v1
kind: PersistentVolume
metadata:
  name: efs-pv-team-a
spec:
  capacity:
    storage: 1Ti              # EFS는 용량 제한 없음, 명시적 선언만
  volumeMode: Filesystem
  accessModes:
    - ReadWriteMany
  persistentVolumeReclaimPolicy: Retain
  storageClassName: efs-team-a
  csi:
    driver: efs.csi.aws.com
    volumeHandle: "fs-0abc123456789::fsap-0team-a-apid"
    #              ↑ EFS File System ID  ↑ Access Point ID
    # 형식: <fileSystemId>::<accessPointId>
  # team-a namespace의 PVC만 바인딩 허용 (보안 강화)
  claimRef:
    namespace: team-a
    name: efs-pvc-team-a
---
# ── team-a namespace PVC ──────────────────────────────────────────
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: efs-pvc-team-a
  namespace: team-a
spec:
  accessModes:
    - ReadWriteMany
  storageClassName: efs-team-a
  resources:
    requests:
      storage: 1Ti
  volumeName: efs-pv-team-a   # 위 PV에 직접 바인딩
---
# ── Pod 마운트 예시 ───────────────────────────────────────────────
apiVersion: v1
kind: Pod
metadata:
  name: app-team-a
  namespace: team-a
spec:
  containers:
    - name: app
      image: nginx:alpine
      volumeMounts:
        - name: efs-storage
          mountPath: /data    # AP 루트(=/teams/team-a)가 /data로 마운트됨
  volumes:
    - name: efs-storage
      persistentVolumeClaim:
        claimName: efs-pvc-team-a
```

#### EKS — 동적 프로비저닝 (PVC 생성 시 AP 자동 생성)

PVC 생성 시마다 Access Point를 자동으로 생성하는 방식.
마이크로서비스 수가 많고 팀별 PVC를 빠르게 생성해야 할 때 적합하다.

```yaml
# ── team-a 동적 StorageClass ──────────────────────────────────────
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: efs-team-a-dynamic
provisioner: efs.csi.aws.com
parameters:
  provisioningMode: efs-ap             # PVC 생성 시 AP 자동 생성
  fileSystemId: fs-0abc123456789
  directoryPerms: "750"
  gidRangeStart: "1000"               # team-a GID 범위 1000~1999
  gidRangeEnd:   "1999"
  basePath: "/teams/team-a"           # AP 경로 prefix (CSI v1.4.0+)
  # uid: "1000"                       # 고정 UID (미지정 시 GID 범위 내 자동 할당)
reclaimPolicy: Delete
volumeBindingMode: Immediate
allowVolumeExpansion: false           # EFS는 확장 불필요 (무제한 용량)
---
# ── team-b 동적 StorageClass ──────────────────────────────────────
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: efs-team-b-dynamic
provisioner: efs.csi.aws.com
parameters:
  provisioningMode: efs-ap
  fileSystemId: fs-0abc123456789
  directoryPerms: "750"
  gidRangeStart: "2000"               # team-b GID 범위 2000~2999
  gidRangeEnd:   "2999"
  basePath: "/teams/team-b"
reclaimPolicy: Delete
volumeBindingMode: Immediate
---
# ── PVC (동적 프로비저닝) ─────────────────────────────────────────
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: team-a-service-pvc
  namespace: team-a
spec:
  accessModes:
    - ReadWriteMany
  storageClassName: efs-team-a-dynamic
  resources:
    requests:
      storage: 5Gi    # EFS 실제 제한 없음, 선언값만
```

#### Lambda — Access Point 마운트

Lambda 함수에서 EFS를 영구 스토리지로 마운트하는 설정.
ML 모델 가중치, 공유 캐시, 대용량 설정 파일 로드에 활용한다.

```hcl
resource "aws_lambda_function" "ml_inference" {
  function_name = "ml-inference"
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  role          = aws_iam_role.lambda.arn
  filename      = "function.zip"

  # EFS 마운트 설정
  file_system_config {
    arn              = aws_efs_access_point.main["app-uploads"].arn
    local_mount_path = "/mnt/models"   # Lambda 내부 마운트 경로
  }

  # EFS와 동일 VPC/Subnet 필수
  vpc_config {
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [aws_security_group.lambda.id]
  }

  depends_on = [aws_efs_mount_target.az]
}

# Lambda → EFS NFS 허용 Security Group
resource "aws_security_group_rule" "lambda_to_efs" {
  type                     = "egress"
  from_port                = 2049
  to_port                  = 2049
  protocol                 = "tcp"
  security_group_id        = aws_security_group.lambda.id
  source_security_group_id = aws_security_group.efs.id
  description              = "Lambda to EFS NFS"
}
```

#### IAM — Access Point 기반 세분화된 접근 제어

EFS 리소스 정책으로 특정 Access Point만 접근 허용하는 패턴.

```hcl
# EFS 파일시스템 정책 — AP를 통해서만 접근 허용 (직접 마운트 차단)
resource "aws_efs_file_system_policy" "main" {
  file_system_id = aws_efs_file_system.main.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # ① 모든 클라이언트의 루트 접근 차단
      {
        Sid    = "DenyRootAccess"
        Effect = "Deny"
        Principal = { AWS = "*" }
        Action    = "elasticfilesystem:ClientRootAccess"
        Resource  = aws_efs_file_system.main.arn
      },
      # ② AP 없이 직접 마운트 차단 (Access Point 강제)
      {
        Sid    = "EnforceAccessPoint"
        Effect = "Deny"
        Principal = { AWS = "*" }
        Action    = [
          "elasticfilesystem:ClientMount",
          "elasticfilesystem:ClientWrite"
        ]
        Resource = aws_efs_file_system.main.arn
        Condition = {
          Bool = {
            "elasticfilesystem:AccessedViaMountTarget" = "true"
          }
          StringNotLike = {
            "elasticfilesystem:AccessPointArn" = "arn:aws:elasticfilesystem:*:*:access-point/fsap-*"
          }
        }
      },
      # ③ EFS CSI 드라이버 (IRSA Role)에 AP 관리 권한
      {
        Sid    = "AllowEFSCSIDriver"
        Effect = "Allow"
        Principal = {
          AWS = module.efs_csi_irsa.iam_role_arn
        }
        Action = [
          "elasticfilesystem:ClientMount",
          "elasticfilesystem:ClientWrite",
          "elasticfilesystem:ClientRootAccess"
        ]
        Resource = aws_efs_file_system.main.arn
      }
    ]
  })
}
```

#### 크로스 계정 EFS Access Point 공유

중앙 스토리지 계정의 EFS를 워크로드 계정에서 접근하는 엔터프라이즈 패턴.

```hcl
# ── 스토리지 계정 (EFS 소유) ──────────────────────────────────────
resource "aws_efs_file_system_policy" "cross_account" {
  file_system_id = aws_efs_file_system.main.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowCrossAccountAccess"
        Effect = "Allow"
        Principal = {
          AWS = [
            "arn:aws:iam::${var.workload_account_id}:root"
          ]
        }
        Action = [
          "elasticfilesystem:ClientMount",
          "elasticfilesystem:ClientWrite",
          "elasticfilesystem:DescribeMountTargets"
        ]
        Resource = aws_efs_file_system.main.arn
        Condition = {
          StringEquals = {
            # 특정 AP를 통해서만 허용
            "elasticfilesystem:AccessPointArn" = aws_efs_access_point.main["team-a"].arn
          }
        }
      }
    ]
  })
}

# ── 워크로드 계정 (EFS 소비) — VPC Peering + DNS 설정 필요 ────────
# aws_vpc_peering_connection, route, dns 설정은 생략
# 마운트 타겟 IP를 직접 사용하거나 Route 53 Private Hosted Zone으로 해결
```

---

### 2.3 보안/비용 Best Practice

**보안**

- **EFS 파일시스템 정책으로 직접 마운트 차단**: `EnforceAccessPoint` 정책으로 AP 없이 마운트 시도 자체를 차단
- **루트 접근 비허용**: `DenyRootAccess` 정책 필수 — AP의 UID/GID 강제 기능을 우회하는 루트 접근 차단
- **전송 중 암호화 강제**: 마운트 시 `tls` 옵션 사용 (`efs.csi.aws.com`은 기본 TLS 사용)
- **저장 중 암호화**: EFS 생성 시 `encrypted = true`, KMS CMK 사용
- **IRSA로 권한 최소화**: EFS CSI 드라이버 ServiceAccount에만 `elasticfilesystem:*` 권한 부여 (노드 IAM Role에 부여 X)
- **namespace 격리**: `claimRef.namespace`로 특정 namespace의 PVC만 PV에 바인딩 허용

**비용**

- **Elastic Throughput 모드 사용**: 프로비저닝된 처리량 모드 대비 사용한 만큼만 과금
- **IA 티어 활용**: `transition_to_ia = "AFTER_30_DAYS"` — 30일 미접근 파일을 IA(Infrequent Access) 티어로 이동, 최대 92% 비용 절감
- **`transition_to_primary_storage_class = "AFTER_1_ACCESS"`**: 재접근 시 Standard로 자동 복귀 (지연 최소화)
- **사용하지 않는 Access Point 정리**: `aws efs describe-access-points`로 주기적 감사

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**Pod가 EFS 마운트 후 Permission Denied**

```bash
# 증상
kubectl logs <pod>
# "Permission denied" 오류 발생

# 원인 확인 — Pod 내 실제 UID 확인
kubectl exec -it <pod> -- id
# uid=0(root) gid=0(root)  ← AP의 posix_user 설정과 불일치

# AP의 posix_user 설정 확인
aws efs describe-access-points \
  --access-point-id fsap-0xxxxxxxxxxxxxxx \
  --query 'AccessPoints[0].PosixUser'
# { "Uid": 1000, "Gid": 1000 }

# 해결 — Deployment에 securityContext 추가
# AP의 UID/GID와 일치시키거나, AP의 posix_user가 강제 적용되므로
# 컨테이너 이미지에서 해당 UID로 파일을 생성한 경우가 아니라면
# runAsUser/fsGroup을 AP 설정과 맞춤
spec:
  securityContext:
    fsGroup: 1000          # AP gid와 일치
    runAsUser: 1000        # AP uid와 일치
    runAsNonRoot: true
```

**EFS 마운트 타임아웃 (mount.nfs: Connection timed out)**

```bash
# 증상
kubectl describe pod <pod>
# "Unable to attach or mount volumes: ... connection timed out"

# 원인 1: Security Group에서 NFS(2049) 포트 미허용
aws ec2 describe-security-groups --group-ids <efs-sg-id> \
  --query 'SecurityGroups[0].IpPermissions'
# 2049 포트 인바운드 규칙 누락 확인

# 원인 2: 마운트 타겟이 해당 AZ에 없음
aws efs describe-mount-targets \
  --file-system-id fs-0xxxxxxxxxxxxxxx \
  --query 'MountTargets[*].{AZ:AvailabilityZoneName,State:LifeCycleState}'

# 원인 3: VPC DNS 해석 비활성화
aws ec2 describe-vpc-attribute --vpc-id vpc-xxx --attribute enableDnsResolution
aws ec2 describe-vpc-attribute --vpc-id vpc-xxx --attribute enableDnsHostnames
# 둘 다 true여야 함

# 확인 명령 (EKS 노드에서 직접 마운트 테스트)
sudo mount -t nfs4 -o nfsvers=4.1,rsize=1048576,wsize=1048576,hard,timeo=600,retrans=2,noresvport \
  <fs-id>.efs.<region>.amazonaws.com:/ /mnt/efs-test
```

**Access Point가 없는 경로에 생성되어 권한 오류**

```bash
# 증상: PVC는 Bound인데 Pod에서 ls: cannot access '/data': No such file or directory

# 원인: Terraform의 creation_info를 누락하여 root_directory 경로가 미생성
# 확인
aws efs describe-access-points \
  --access-point-id fsap-0xxxxxxxxxxxxxxx \
  --query 'AccessPoints[0].RootDirectory'
# "Path": "/teams/team-a", "CreationInfo": null  ← 자동 생성 설정 없음

# 해결: Terraform에 creation_info 추가 후 재apply
root_directory {
  path = "/teams/team-a"
  creation_info {
    owner_uid   = 1000
    owner_gid   = 1000
    permissions = "750"
  }
}
```

**동적 프로비저닝 — PVC Pending 상태 지속**

```bash
# 증상
kubectl get pvc -n team-a
# NAME                  STATUS    ...
# team-a-service-pvc    Pending

kubectl describe pvc team-a-service-pvc -n team-a
# "failed to provision volume ... AccessDeniedException"

# 원인: EFS CSI 드라이버 IRSA Role에 elasticfilesystem:CreateAccessPoint 권한 부족
# 확인
aws iam simulate-principal-policy \
  --policy-source-arn <efs-csi-role-arn> \
  --action-names elasticfilesystem:CreateAccessPoint

# 해결: attach_efs_csi_policy = true (terraform-aws-modules 사용 시 자동 포함)
# 수동 추가 시 필요한 권한:
# elasticfilesystem:CreateAccessPoint
# elasticfilesystem:DeleteAccessPoint
# elasticfilesystem:DescribeAccessPoints
# elasticfilesystem:DescribeFileSystems
# elasticfilesystem:DescribeMountTargets
```

**volumeHandle 형식 오류**

```bash
# 증상: PV가 생성되었으나 PVC 바인딩 실패
# "invalid volumeHandle format"

# 올바른 형식:
# fs-<id>::fsap-<id>    ← 두 개의 콜론(::)으로 구분
# 잘못된 형식:
# fs-<id>:fsap-<id>     ← 콜론 하나 (오류)
# fs-<id>/fsap-<id>     ← 슬래시 (오류)

csi:
  driver: efs.csi.aws.com
  volumeHandle: "fs-0abc123456789::fsap-0def789abc"  # ← :: 두 개 콜론
```

---

### 3.2 자주 발생하는 문제 (Q&A)

**Q: 동적 프로비저닝으로 생성된 Access Point가 너무 많아져서 관리가 어렵습니다**

A: PVC `reclaimPolicy: Delete` 설정 시 PVC 삭제와 함께 AP가 자동 삭제됩니다.
`reclaimPolicy: Retain`이면 AP가 남으므로 주기적으로 정리가 필요합니다.

```bash
# 현재 Access Point 목록 조회
aws efs describe-access-points \
  --file-system-id fs-0xxxxxxxxxxxxxxx \
  --query 'AccessPoints[*].{ID:AccessPointId,Path:RootDirectory.Path,State:LifeCycleState}'

# Released 상태 PV (PVC 없이 남은 PV) 확인
kubectl get pv | grep Released

# 정리
kubectl delete pv <released-pv-name>
```

**Q: EFS를 Fargate Pod에 마운트하려면 어떻게 해야 하나요?**

A: Fargate는 EFS 마운트를 지원하지만 제약이 있습니다. Access Point 사용이 **필수**이며 정적 프로비저닝만 가능합니다. (Fargate에서 동적 프로비저닝 미지원)

```yaml
# Fargate + EFS 정적 PV 예시
spec:
  csi:
    driver: efs.csi.aws.com
    volumeHandle: "fs-0abc::fsap-0def"   # AP 없이 마운트 불가
```

**Q: 여러 Pod가 같은 Access Point를 동시에 마운트할 수 있나요?**

A: 가능합니다. EFS는 ReadWriteMany(RWX)를 지원하므로 동일 AP에 여러 Pod가 동시 읽기/쓰기할 수 있습니다. 단, 동일 파일에 여러 Pod가 동시에 쓰면 NFS 특성상 충돌이 발생할 수 있으므로 애플리케이션 레벨에서 파일 잠금(flock)을 고려해야 합니다.

**Q: Access Point 루트 경로 변경이 가능한가요?**

A: 불가능합니다. 루트 경로는 변경 불가 속성입니다. 새 경로가 필요하면 새 Access Point를 생성하고, 기존 데이터를 복사한 뒤 교체해야 합니다.

---

## 4. 모니터링 및 알람

**CloudWatch 주요 지표**

| 지표 | 네임스페이스 | 설명 |
|------|-------------|------|
| `StorageBytes` | `AWS/EFS` | 파일시스템 사용 용량 (Standard/IA 분리) |
| `ClientConnections` | `AWS/EFS` | 동시 NFS 연결 수 |
| `DataReadIOBytes` | `AWS/EFS` | 읽기 처리량 (Bytes/sec) |
| `DataWriteIOBytes` | `AWS/EFS` | 쓰기 처리량 (Bytes/sec) |
| `PermittedThroughput` | `AWS/EFS` | 허용된 최대 처리량 (Elastic 모드에서 동적) |
| `MeteredIOBytes` | `AWS/EFS` | 청구 대상 IO (처리량 과금 계산용) |
| `BurstCreditBalance` | `AWS/EFS` | 버스트 크레딧 (Bursting 모드에서만 해당) |

**CloudWatch 알람 설정 예시**

```hcl
# 처리량 한도 도달 알람 (Elastic 모드에서도 최대 한도 있음)
resource "aws_cloudwatch_metric_alarm" "efs_throughput_high" {
  alarm_name          = "${var.cluster_name}-efs-throughput-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "MeteredIOBytes"
  namespace           = "AWS/EFS"
  period              = 60
  statistic           = "Sum"
  threshold           = 500 * 1024 * 1024  # 500 MiB/s
  alarm_description   = "EFS 처리량 500MiB/s 초과"

  dimensions = {
    FileSystemId = aws_efs_file_system.main.id
  }

  alarm_actions = [var.sns_topic_arn]
}

# 연결 수 급증 알람 (NFS 클라이언트 과다 연결 탐지)
resource "aws_cloudwatch_metric_alarm" "efs_connections_high" {
  alarm_name          = "${var.cluster_name}-efs-connections-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "ClientConnections"
  namespace           = "AWS/EFS"
  period              = 300
  statistic           = "Sum"
  threshold           = 1000
  alarm_description   = "EFS 동시 연결 수 1000 초과"

  dimensions = {
    FileSystemId = aws_efs_file_system.main.id
  }

  alarm_actions = [var.sns_topic_arn]
}
```

**Kubernetes PVC 사용률 알람 (Prometheus)**

```yaml
# EFS PVC는 kubelet_volume_stats 지표 미지원 (NFS 특성)
# 대신 EFS 파일시스템 레벨 지표를 Container Insights로 수집

# CloudWatch Logs Insights — EFS 마운트 오류 탐지
fields @timestamp, @message
| filter @logStream like /efs-csi/
| filter @message like /error|failed|timeout/
| sort @timestamp desc
| limit 50
```

**Access Point 사용 현황 감사 (AWS CLI)**

```bash
# 전체 Access Point 목록 및 상태
aws efs describe-access-points \
  --file-system-id fs-0xxxxxxxxxxxxxxx \
  --query 'AccessPoints[*].{
    ID: AccessPointId,
    State: LifeCycleState,
    Path: RootDirectory.Path,
    UID: PosixUser.Uid,
    GID: PosixUser.Gid,
    Tags: Tags
  }' \
  --output table

# CloudTrail로 AP 생성/삭제 이벤트 조회
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=CreateAccessPoint \
  --start-time "2024-01-01T00:00:00Z" \
  --query 'Events[*].{Time:EventTime,User:Username,AP:CloudTrailEvent}'
```

---

## 5. TIP

- **volumeHandle 형식은 `fs-xxx::fsap-xxx`** — 콜론 2개(`::`)가 파일시스템 ID와 AP ID를 구분한다. 콜론 1개로 작성하면 CSI 드라이버가 오류를 낸다.
- **AP의 `creation_info`는 멱등성 보장** — Terraform으로 재apply해도 이미 디렉터리가 존재하면 권한만 검증하고 재생성하지 않는다.
- **Elastic Throughput 모드** — 워크로드 패턴이 불규칙하면 Provisioned 모드보다 Elastic이 훨씬 저렴하다. 특히 EKS CI/CD 빌드처럼 짧게 대량 IO가 몰리는 경우 Elastic이 적합하다.
- **Fargate + EFS 조합 시 subnets 주의** — Fargate Pod가 배포되는 서브넷에 반드시 EFS 마운트 타겟이 존재해야 한다. 마운트 타겟 없이 연결 시도하면 indefinitely hang 현상이 발생한다.
- **Access Point 당 연결 수 제한 없음** — AP 자체에는 연결 수 제한이 없다. 파일시스템 레벨에서 최대 25,000개 NFS 연결을 지원한다.
- **삭제 전 마운트 해제 확인** — 연결 중인 클라이언트가 있으면 AP 삭제 시 `FileSystemInUse` 오류가 발생한다. `aws efs describe-mount-targets` + `ClientConnections` 지표 확인 후 삭제한다.
- 관련 문서: [EFS Access Points 공식 문서](https://docs.aws.amazon.com/efs/latest/ug/efs-access-points.html), [EFS CSI 드라이버 GitHub](https://github.com/kubernetes-sigs/aws-efs-csi-driver)
