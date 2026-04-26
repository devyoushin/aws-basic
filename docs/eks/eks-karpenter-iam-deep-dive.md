# Karpenter IAM 권한 획득 및 노드 프로비저닝 동작 원리

## 1. 개요

Karpenter는 EKS 클러스터 안에서 실행되는 **일반 Pod**다.
Pod가 EC2 인스턴스를 생성하려면 AWS API를 호출해야 하고, 그러려면 IAM 자격 증명이 필요하다.
이 문서는 "IAM 권한이 어떻게 Karpenter Pod 안으로 들어오고, 어떻게 EC2 노드를 올리는지"를 토큰 주입 → 자격 증명 교환 → API 호출 → 노드 부트스트랩까지 전 과정을 설명한다.

---

## 2. 핵심 구조: IAM Role이 두 개 존재한다

Karpenter 환경에는 목적이 다른 IAM Role이 두 개 필요하다.

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│  ① Karpenter Controller Role  (IRSA)                           │
│     → Karpenter Pod가 사용                                       │
│     → AWS API 호출 권한 (EC2 생성, IAM PassRole, SSM, Pricing)  │
│                                                                 │
│  ② Karpenter Node Role  (EC2 Instance Profile)                 │
│     → Karpenter가 생성한 EC2 노드가 사용                         │
│     → 노드가 EKS 클러스터에 조인하고 운영되기 위한 권한           │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

| 구분 | 사용 주체 | 권한 예시 | 전달 방식 |
|------|-----------|-----------|-----------|
| Controller Role | Karpenter Pod | ec2:RunInstances, iam:PassRole | IRSA (OIDC 토큰) |
| Node Role | EC2 인스턴스 | eks:DescribeCluster, ecr:GetToken | Instance Profile |

---

## 3. 사전 설정 — IAM Role 구성

### 3.1 Controller Role (Karpenter Pod용)

```hcl
# Karpenter Controller가 사용할 IAM Role
resource "aws_iam_role" "karpenter_controller" {
  name = "karpenter-controller-${var.cluster_name}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        # EKS OIDC Provider를 신뢰 — Karpenter ServiceAccount만 허용
        Federated = aws_iam_openid_connect_provider.eks.arn
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          # "kube-system 네임스페이스의 karpenter SA만 이 Role을 가정할 수 있다"
          "${local.oidc_url}:sub" = "system:serviceaccount:kube-system:karpenter"
          "${local.oidc_url}:aud" = "sts.amazonaws.com"
        }
      }
    }]
  })
}

# Karpenter가 EC2를 직접 제어하기 위한 권한
resource "aws_iam_role_policy" "karpenter_controller" {
  name = "karpenter-controller-policy"
  role = aws_iam_role.karpenter_controller.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # EC2 인스턴스 생성/조회/삭제
        Effect = "Allow"
        Action = [
          "ec2:RunInstances",
          "ec2:DescribeInstances",
          "ec2:DescribeInstanceTypes",
          "ec2:DescribeSubnets",
          "ec2:DescribeSecurityGroups",
          "ec2:DescribeLaunchTemplates",
          "ec2:TerminateInstances",
          "ec2:CreateFleet",
          "ec2:DescribeSpotPriceHistory",
          "ec2:CreateLaunchTemplate",
          "ec2:DeleteLaunchTemplate",
          "ec2:CreateTags"
        ]
        Resource = "*"
      },
      {
        # Node Role을 EC2 인스턴스에 붙이기 위한 PassRole
        # PassRole 없이는 RunInstances에서 IAM Instance Profile 지정 불가
        Effect   = "Allow"
        Action   = "iam:PassRole"
        Resource = aws_iam_role.karpenter_node.arn
      },
      {
        # Spot 인터럽션, Rebalance 알림 수신 (SQS)
        Effect = "Allow"
        Action = ["sqs:ReceiveMessage", "sqs:DeleteMessage"]
        Resource = aws_sqs_queue.karpenter_interruption.arn
      },
      {
        # 인스턴스 타입별 가격 정보 조회 (최적 인스턴스 선택)
        Effect   = "Allow"
        Action   = "pricing:GetProducts"
        Resource = "*"
      },
      {
        # SSM에서 최신 EKS 최적화 AMI ID 조회
        Effect   = "Allow"
        Action   = "ssm:GetParameter"
        Resource = "arn:aws:ssm:*:*:parameter/aws/service/eks/optimized-ami/*"
      }
    ]
  })
}
```

### 3.2 Node Role (EC2 인스턴스용)

```hcl
# Karpenter가 생성한 EC2 노드가 사용할 IAM Role
resource "aws_iam_role" "karpenter_node" {
  name = "karpenter-node-${var.cluster_name}"

  # EC2 서비스가 이 Role을 가정 (일반 EC2 Instance Profile 방식)
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# EKS 노드에 필요한 관리형 정책 4개
resource "aws_iam_role_policy_attachment" "karpenter_node_policies" {
  for_each = toset([
    "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",       # EKS 클러스터 통신
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly", # ECR 이미지 Pull
    "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",            # VPC CNI (IP 할당)
    "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"     # SSM Session Manager
  ])
  role       = aws_iam_role.karpenter_node.name
  policy_arn = each.value
}

# EC2 Instance Profile — EC2 인스턴스에 IAM Role을 붙이는 래퍼(wrapper)
resource "aws_iam_instance_profile" "karpenter_node" {
  name = "karpenter-node-${var.cluster_name}"
  role = aws_iam_role.karpenter_node.name
}
```

---

## 4. /var/run이란 무엇인가

Karpenter Pod에 토큰이 어떻게 들어가는지 이해하려면 먼저 `/var/run`을 알아야 한다.

### 4.1 Linux 파일시스템에서 /var/run의 역할

```
/
├── var/
│   ├── run -> /run    ← 현대 Linux에서 /var/run은 /run의 심볼릭 링크
│   ├── log/           ← 로그 파일 (디스크 영구 저장)
│   └── lib/           ← 상태 파일 (디스크 영구 저장)
└── run/               ← 실제 디렉토리 (tmpfs — RAM에만 존재)
    ├── containerd/
    │   └── containerd.sock   ← containerd Unix 소켓
    ├── docker.sock           ← Docker 데몬 소켓
    ├── sshd.pid              ← sshd 프로세스 PID 파일
    └── secrets/              ← Kubernetes가 토큰을 주입하는 경로
```

**tmpfs (Temporary FileSystem)란?**

```bash
# /run이 tmpfs임을 확인
mount | grep /run
# tmpfs on /run type tmpfs (rw,nosuid,nodev,noexec,relatime,size=...)

# 또는
df -T /run
# Filesystem     Type  1K-blocks  Used Available Use% Mounted on
# tmpfs          tmpfs    ...
```

| 특성 | 설명 |
|------|------|
| 저장 위치 | RAM (메모리) — 디스크에 쓰지 않는다 |
| 재부팅 후 | 완전히 초기화됨 (내용 사라짐) |
| 속도 | 디스크 I/O 없음 → 매우 빠름 |
| 보안 | 디스크에 기록되지 않아 포렌식에 남지 않음 |
| 용도 | PID 파일, Unix 소켓, Lock 파일, **임시 자격 증명** |

**Kubernetes가 `/var/run/secrets/`를 선택한 이유:**
- 토큰이 디스크에 영구 저장되면 보안 위험 → tmpfs에만 존재
- Pod 삭제 시 자동으로 사라짐 (메모리에만 있으므로)
- kubelet이 주기적으로 갱신해도 I/O 부담 없음

### 4.2 컨테이너 안에서 /var/run

컨테이너는 호스트 OS의 네임스페이스를 공유하거나 격리하여 실행된다.
파일시스템은 격리되므로 컨테이너 안의 `/var/run`은 호스트와 다른 독립 공간이다.

```
호스트 EC2 노드
├── /run/                        (호스트 tmpfs)
│   ├── containerd/containerd.sock
│   └── kubelet/                 ← kubelet이 관리하는 volume 데이터
│       └── pods/<pod-uid>/
│           └── volumes/
│               └── kubernetes.io~projected/
│                   └── aws-iam-token/
│                       └── token   ← kubelet이 여기에 JWT 파일을 씀
│
└── 컨테이너 (네임스페이스 격리)
    └── /var/run/secrets/             (컨테이너 내부 — bind mount)
        └── eks.amazonaws.com/
            └── serviceaccount/
                └── token             ← 위의 호스트 경로가 여기로 bind mount됨
```

kubelet은 호스트의 tmpfs 경로에 JWT를 쓰고, 컨테이너 안의 `/var/run/secrets/...`로 bind mount한다.
컨테이너 입장에서는 `/var/run/secrets/eks.amazonaws.com/serviceaccount/token`을 그냥 읽으면 된다.

---

## 5. 토큰이 /var/run에 들어오는 과정

### 5.1 전체 흐름

```
┌─────────────────────────────────────────────────────────────────────┐
│                   토큰 주입 전체 흐름                                 │
└─────────────────────────────────────────────────────────────────────┘

[사전 설정]
  ① EKS 클러스터 생성 → OIDC Issuer URL 자동 부여
     https://oidc.eks.ap-northeast-2.amazonaws.com/id/<CLUSTER_ID>

  ② IAM에 OIDC Provider 등록
     → "이 URL이 서명한 JWT를 신뢰하겠다"고 AWS에 등록

  ③ Karpenter Helm 배포 시 ServiceAccount 생성
     → annotation: eks.amazonaws.com/role-arn: arn:aws:iam::...:role/karpenter-controller

[런타임 — Karpenter Pod 시작 시]

  kubectl apply (Helm install)
         │
         ▼
  ┌──────────────────────────────────┐
  │  Kubernetes API Server           │
  │                                  │
  │  ServiceAccount annotation 감지  │
  │  eks.amazonaws.com/role-arn 있음 │
  └──────────┬───────────────────────┘
             │
             │ ① Mutating Webhook (EKS Pod Identity Webhook) 발동
             │    → Pod spec에 자동으로 두 가지를 추가:
             │
             │    환경변수 추가:
             │    AWS_WEB_IDENTITY_TOKEN_FILE=
             │      /var/run/secrets/eks.amazonaws.com/serviceaccount/token
             │    AWS_ROLE_ARN=
             │      arn:aws:iam::123456789012:role/karpenter-controller
             │
             │    Projected Volume 추가:
             │    volumes:
             │      - name: aws-iam-token
             │        projected:
             │          sources:
             │            - serviceAccountToken:
             │                audience: sts.amazonaws.com
             │                expirationSeconds: 86400
             │                path: token
             │
             ▼
  ┌──────────────────────────────────┐
  │  kubelet (노드 에이전트)          │
  │                                  │
  │  ② TokenRequest API 호출         │
  │     → Kubernetes API Server에    │
  │       "karpenter SA용 JWT 발급   │
  │        해줘 (audience:           │
  │        sts.amazonaws.com,        │
  │        만료: 86400초)"           │
  │                                  │
  │  ③ JWT를 호스트 tmpfs에 저장     │
  │     /run/kubelet/pods/<uid>/     │
  │     volumes/.../token            │
  │                                  │
  │  ④ 컨테이너 시작 시 bind mount   │
  │     → 컨테이너 내부의            │
  │     /var/run/secrets/            │
  │     eks.amazonaws.com/           │
  │     serviceaccount/token 으로    │
  └──────────────────────────────────┘

[결과 — Karpenter 컨테이너 내부]
  $ cat /var/run/secrets/eks.amazonaws.com/serviceaccount/token
  eyJhbGciOiJSUzI1NiIsImtpZCI6Ii...  (JWT)

  $ env | grep AWS
  AWS_WEB_IDENTITY_TOKEN_FILE=/var/run/secrets/eks.amazonaws.com/serviceaccount/token
  AWS_ROLE_ARN=arn:aws:iam::123456789012:role/karpenter-controller
```

### 5.2 JWT 내부 구조

kubelet이 발급하는 JWT(Projected Token)의 페이로드:

```json
{
  "iss": "https://oidc.eks.ap-northeast-2.amazonaws.com/id/XXXXX",
  "sub": "system:serviceaccount:kube-system:karpenter",
  "aud": ["sts.amazonaws.com"],
  "exp": 1745712345,
  "iat": 1745625945,
  "kubernetes.io": {
    "namespace": "kube-system",
    "serviceaccount": { "name": "karpenter", "uid": "..." },
    "pod": { "name": "karpenter-7d9f8c-xxxxx", "uid": "..." }
  }
}
```

| 클레임 | 의미 |
|--------|------|
| `iss` | 발급자 — EKS OIDC Provider URL (STS가 서명 검증에 사용) |
| `sub` | 주체 — Trust Policy Condition과 정확히 일치해야 함 |
| `aud` | 대상 — `sts.amazonaws.com`만 허용 (다른 서비스 사용 불가) |
| `exp` | 만료 시간 — 기본 86400초(24h), kubelet이 만료 전 자동 갱신 |

---

## 6. AWS SDK가 토큰을 자격 증명으로 교환하는 과정

### 6.1 자격 증명 교환 흐름

```
Karpenter 컨테이너 (Go 코드)
  │
  │  AWS SDK 초기화 시 자격 증명 Provider Chain 순서대로 탐색:
  │  1. 환경변수 (AWS_ACCESS_KEY_ID) → 없음
  │  2. ~/.aws/credentials → 없음
  │  3. EC2 Instance Metadata (IMDS) → Karpenter Pod는 건너뜀
  │  4. **WebIdentity Token** → AWS_WEB_IDENTITY_TOKEN_FILE 환경변수 발견!
  │
  │ ① 파일 읽기
  │    token = readFile("/var/run/secrets/eks.amazonaws.com/serviceaccount/token")
  │
  │ ② STS 호출
  │
  ▼
┌──────────────────────────────────────────────────────────┐
│  AWS STS (Security Token Service)                        │
│  https://sts.ap-northeast-2.amazonaws.com                │
│                                                          │
│  AssumeRoleWithWebIdentity(                              │
│    RoleArn           = $AWS_ROLE_ARN,                    │
│    RoleSessionName   = "karpenter-session",              │
│    WebIdentityToken  = <JWT 문자열>                      │
│  )                                                       │
│                                                          │
│  STS 내부 검증:                                           │
│  ① JWT Header의 kid(Key ID) 추출                         │
│  ② JWT iss 클레임 → OIDC Provider URL 확인              │
│     → IAM에 등록된 Provider인지 확인                     │
│  ③ https://{oidc_issuer}/.well-known/openid-configuration│
│     → jwks_uri 조회 → 공개키 목록(JWKS) 가져옴          │
│  ④ kid에 맞는 공개키로 JWT 서명 검증 (RS256)            │
│  ⑤ Trust Policy Condition 매칭:                         │
│     sub == "system:serviceaccount:kube-system:karpenter" │
│     aud == "sts.amazonaws.com"                           │
│  ⑥ exp 확인 → 만료되지 않았으면 통과                    │
└──────────────────────┬───────────────────────────────────┘
                       │ 임시 자격 증명 발급 (기본 1시간)
                       ▼
                 AccessKeyId:     ASIA...
                 SecretAccessKey: xxxxxx
                 SessionToken:    xxxxxx (STS 임시 토큰)
                 Expiration:      2026-04-26T11:00:00Z

Karpenter AWS SDK
  │  ③ 자격 증명 메모리 캐싱
  │  ④ 만료 5분 전 SDK가 자동으로 재호출 (토큰 갱신)
  │
  ▼
  이후 모든 AWS API 호출에 이 자격 증명 사용
  (SigV4 서명 → Authorization 헤더에 포함)
```

### 6.2 실제 확인 명령어

```bash
# Karpenter Pod 내부에서 확인
kubectl exec -n kube-system deploy/karpenter -- sh -c '
  echo "=== 환경변수 ==="
  env | grep AWS

  echo "=== JWT 토큰 (디코딩) ==="
  TOKEN=$(cat $AWS_WEB_IDENTITY_TOKEN_FILE)
  echo $TOKEN | cut -d. -f2 | base64 -d 2>/dev/null

  echo "=== 현재 자격 증명 (AssumedRole 확인) ==="
  aws sts get-caller-identity
'

# 출력 예시:
# AWS_WEB_IDENTITY_TOKEN_FILE=/var/run/secrets/eks.amazonaws.com/serviceaccount/token
# AWS_ROLE_ARN=arn:aws:iam::123456789012:role/karpenter-controller-my-cluster
#
# {
#   "iss": "https://oidc.eks.ap-northeast-2.amazonaws.com/id/XXXXX",
#   "sub": "system:serviceaccount:kube-system:karpenter",
#   ...
# }
#
# {
#   "UserId": "AROA...:karpenter-session",
#   "Account": "123456789012",
#   "Arn": "arn:aws:sts::123456789012:assumed-role/karpenter-controller-my-cluster/karpenter-session"
# }

# kubelet이 토큰을 갱신하는 주기 확인 (만료 80% 시점에 갱신)
# 기본 86400초 → 약 19.2시간마다 갱신
kubectl exec -n kube-system deploy/karpenter -- sh -c '
  TOKEN=$(cat $AWS_WEB_IDENTITY_TOKEN_FILE)
  echo $TOKEN | cut -d. -f2 | base64 -d 2>/dev/null | python3 -c "
import json, sys, datetime
d = json.load(sys.stdin)
exp = datetime.datetime.fromtimestamp(d[\"exp\"])
iat = datetime.datetime.fromtimestamp(d[\"iat\"])
print(f\"발급: {iat}\")
print(f\"만료: {exp}\")
print(f\"유효기간: {exp - iat}\")
"
'
```

---

## 7. Karpenter가 EC2 노드를 올리는 과정

### 7.1 전체 프로비저닝 흐름

```
Pending Pod 발생 (리소스 부족)
  │
  ▼
┌──────────────────────────────────────────────────────────────────┐
│  Karpenter Controller (Pod)                                      │
│                                                                  │
│  ① Pending Pod 감지                                              │
│     → Pod의 nodeSelector, affinity, resource requests 분석      │
│                                                                  │
│  ② 최적 인스턴스 타입 결정                                        │
│     → NodePool/EC2NodeClass 설정 참조                           │
│     → EC2 API DescribeSpotPriceHistory 호출 (Spot 가격 조회)    │
│     → Pricing API GetProducts 호출 (On-Demand 가격 조회)        │
│     → 가장 저렴하고 적합한 인스턴스 타입 선택                    │
│                                                                  │
│  ③ AMI ID 조회                                                   │
│     → SSM GetParameter 호출                                      │
│     → /aws/service/eks/optimized-ami/1.30/amazon-linux-2/       │
│        recommended/image_id                                      │
│                                                                  │
│  ④ EC2 인스턴스 생성 요청 (핵심)                                 │
│     → ec2:RunInstances 또는 ec2:CreateFleet 호출                │
└──────────────────────────────────────────────────────────────────┘
  │
  │  RunInstances 요청 파라미터:
  │  {
  │    ImageId: "ami-0xxxxx",           ← SSM에서 조회한 EKS 최적화 AMI
  │    InstanceType: "m5.large",
  │    MinCount: 1, MaxCount: 1,
  │    SubnetId: "subnet-xxxxx",        ← EC2NodeClass에 지정된 서브넷
  │    SecurityGroupIds: ["sg-xxxxx"],
  │    IamInstanceProfile: {            ← Node Role (IAM PassRole로 가능)
  │      Arn: "arn:aws:iam::...:instance-profile/karpenter-node-my-cluster"
  │    },
  │    UserData: "<base64 부트스트랩 스크립트>",
  │    TagSpecifications: [{
  │      ResourceType: "instance",
  │      Tags: [
  │        {Key: "karpenter.sh/provisioner-name", Value: "general-purpose"},
  │        {Key: "kubernetes.io/cluster/my-cluster", Value: "owned"},
  │        ...
  │      ]
  │    }]
  │  }
  │
  ▼
AWS EC2 서비스
  → 인스턴스 생성
  → Instance Profile (Node Role) 자동 부착
  → UserData 실행 준비
```

### 7.2 UserData (부트스트랩 스크립트)

Karpenter가 RunInstances에 넘기는 UserData는 노드가 EKS 클러스터에 조인하도록 한다.

```bash
#!/bin/bash
# AL2 기반 EKS 최적화 AMI의 부트스트랩 스크립트
/etc/eks/bootstrap.sh my-cluster \
  --kubelet-extra-args "--node-labels=karpenter.sh/provisioner-name=general-purpose" \
  --b64-cluster-ca "LS0tLS1..." \
  --apiserver-endpoint "https://XXXX.gr7.ap-northeast-2.eks.amazonaws.com"
```

### 7.3 노드 조인 및 Instance Profile 활용

```
EC2 인스턴스 부팅
  │
  │  ① IMDS(Instance Metadata Service)에서 Instance Profile 자격 증명 획득
  │     curl http://169.254.169.254/latest/meta-data/iam/security-credentials/karpenter-node
  │     → AccessKeyId, SecretAccessKey, SessionToken 응답
  │
  │  ② kubelet이 Node Role 자격 증명으로 eks:DescribeCluster 호출
  │     → 클러스터 엔드포인트, CA 정보 확인
  │
  │  ③ kubelet → EKS API Server에 Node 등록 (TLS bootstrap)
  │     → Node 객체 생성: kubectl get node
  │
  │  ④ VPC CNI (aws-node DaemonSet) 시작
  │     → Node Role의 AmazonEKS_CNI_Policy 권한으로 ENI/IP 할당
  │
  │  ⑤ Karpenter가 새 Node 감지 → Pending Pod 스케줄링
  │
  ▼
노드 Ready, Pod 실행 시작
```

---

## 8. EC2NodeClass 설정 (Karpenter v1beta1)

```yaml
apiVersion: karpenter.k8s.aws/v1beta1
kind: EC2NodeClass
metadata:
  name: default
spec:
  # EKS 최적화 AMI 자동 선택 (SSM 조회)
  amiFamily: AL2

  # Node Role — RunInstances 시 Instance Profile로 사용됨
  # Terraform의 aws_iam_instance_profile 이름과 일치해야 함
  role: "karpenter-node-my-cluster"

  # 서브넷 선택 (태그 기반)
  subnetSelectorTerms:
    - tags:
        karpenter.sh/discovery: "my-cluster"

  # 보안 그룹 선택 (태그 기반)
  securityGroupSelectorTerms:
    - tags:
        karpenter.sh/discovery: "my-cluster"

  # 루트 볼륨 설정
  blockDeviceMappings:
    - deviceName: /dev/xvda
      ebs:
        volumeSize: 50Gi
        volumeType: gp3
        encrypted: true

  # UserData 추가 설정 (선택)
  userData: |
    #!/bin/bash
    echo "Custom bootstrap script"
```

---

## 9. iam:PassRole의 역할

`iam:PassRole`은 Karpenter Controller Role에 필수적으로 있어야 하는 권한이다.

```
문제 상황:
  Karpenter Pod가 ec2:RunInstances를 호출하면서
  IamInstanceProfile에 Node Role을 지정하려고 한다.

  이때 AWS는 묻는다:
  "너(Karpenter Controller Role)가 다른 Role(Node Role)을
   EC2 인스턴스에 전달(Pass)할 권한이 있니?"

  이 권한이 iam:PassRole이다.
  없으면 → AccessDenied: Not authorized to perform sts:AssumeRole (ec2.amazonaws.com)

해결:
  Karpenter Controller Role의 정책에 추가:
  {
    "Effect": "Allow",
    "Action": "iam:PassRole",
    "Resource": "arn:aws:iam::123456789012:role/karpenter-node-my-cluster"
  }
```

---

## 10. 전체 흐름 요약 다이어그램

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      Karpenter 전체 IAM 흐름                             │
└─────────────────────────────────────────────────────────────────────────┘

[설정 단계]
  EKS 클러스터 OIDC Provider → IAM에 등록
  Karpenter Controller Role (Trust Policy: OIDC + kube-system:karpenter SA)
  Karpenter Node Role (Instance Profile)
  EC2NodeClass에 Node Role 이름 지정

[Karpenter Pod 시작]
  ServiceAccount annotation 감지
         │
         ▼
  EKS Pod Identity Webhook
  → 환경변수 주입: AWS_ROLE_ARN, AWS_WEB_IDENTITY_TOKEN_FILE
  → Projected Volume 추가 (kubelet이 JWT 발급)
         │
         ▼
  kubelet → TokenRequest API → JWT 발급
  → 호스트 tmpfs에 저장
  → 컨테이너 /var/run/secrets/.../token 으로 bind mount

[Karpenter AWS API 호출]
  AWS SDK 기동
  → JWT 읽기 (/var/run/secrets/eks.amazonaws.com/serviceaccount/token)
  → STS AssumeRoleWithWebIdentity
  → JWT 서명 검증 (OIDC 공개키)
  → Trust Policy 조건 확인 (sub, aud)
  → 임시 자격 증명 발급 (1시간)
         │
         ▼
  Pending Pod 발생 감지
  → EC2 가격/스펙 조회 (Pricing, EC2 API)
  → SSM에서 AMI ID 조회
  → ec2:RunInstances 호출 (+ IamInstanceProfile 지정)
    ※ iam:PassRole이 있어야 Instance Profile 지정 가능

[EC2 노드 부팅]
  Instance Profile → IMDS에서 Node Role 임시 자격 증명 획득
  → EKS 클러스터에 노드 조인 (kubelet bootstrap)
  → VPC CNI → ENI/IP 할당
  → Pending Pod 스케줄링 완료
```

---

## 11. 트러블슈팅

### 11.1 Controller Role 권한 문제

```bash
# Karpenter 로그에서 권한 오류 확인
kubectl logs -n kube-system -l app.kubernetes.io/name=karpenter \
  --since=10m | grep -i "access denied\|unauthorized\|not authorized"

# 임시 자격 증명 확인 (AssumedRole 여부)
kubectl exec -n kube-system deploy/karpenter -- \
  aws sts get-caller-identity

# PassRole 오류 발생 시 — Node Role ARN 확인
kubectl exec -n kube-system deploy/karpenter -- \
  aws iam get-role --role-name karpenter-node-my-cluster \
  --query 'Role.Arn'
```

### 11.2 토큰 관련 문제

```bash
# JWT가 마운트되었는지 확인
kubectl exec -n kube-system deploy/karpenter -- \
  ls -la /var/run/secrets/eks.amazonaws.com/serviceaccount/

# JWT 만료 시간 확인
kubectl exec -n kube-system deploy/karpenter -- sh -c '
  TOKEN=$(cat $AWS_WEB_IDENTITY_TOKEN_FILE)
  echo $TOKEN | cut -d. -f2 | base64 -d 2>/dev/null
'

# OIDC Provider 등록 여부 확인
aws iam list-open-id-connect-providers
aws eks describe-cluster --name my-cluster \
  --query 'cluster.identity.oidc.issuer'
```

### 11.3 노드가 조인하지 못하는 경우

```bash
# EC2 인스턴스의 UserData 실행 로그 확인 (SSM으로 접속)
aws ssm start-session --target <instance-id>
sudo journalctl -u cloud-final -f
sudo cat /var/log/cloud-init-output.log

# Node Role에 EKSWorkerNodePolicy 부착 여부 확인
aws iam list-attached-role-policies \
  --role-name karpenter-node-my-cluster

# Instance Profile과 Role 연결 확인
aws iam get-instance-profile \
  --instance-profile-name karpenter-node-my-cluster
```

---

## 12. 모니터링

```bash
# Karpenter가 실제로 사용하는 AWS API 호출 내역 (CloudTrail)
aws logs filter-log-events \
  --log-group-name "aws-cloudtrail-logs-my-account" \
  --filter-pattern '{ $.userIdentity.sessionContext.sessionIssuer.userName = "karpenter-controller-*" }'
```

```sql
-- Karpenter가 호출한 EC2 API 현황 (Athena)
SELECT
  eventTime,
  eventName,
  errorCode,
  requestParameters,
  userIdentity.sessionContext.sessionIssuer.userName as role_name
FROM cloudtrail_logs
WHERE userIdentity.sessionContext.sessionIssuer.userName LIKE 'karpenter-controller%'
  AND eventTime > date_add('hour', -24, current_timestamp)
ORDER BY eventTime DESC
LIMIT 100;
```

---

## 13. 참고자료

- [Karpenter Getting Started - AWS](https://karpenter.sh/docs/getting-started/getting-started-with-karpenter/)
- [EKS IRSA 공식 문서](https://docs.aws.amazon.com/eks/latest/userguide/iam-roles-for-service-accounts.html)
- [STS AssumeRoleWithWebIdentity API](https://docs.aws.amazon.com/STS/latest/APIReference/API_AssumeRoleWithWebIdentity.html)
- [Linux tmpfs man page](https://man7.org/linux/man-pages/man5/tmpfs.5.html)
