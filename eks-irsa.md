# IAM Roles for Service Accounts (IRSA)

## 1. 개요

IRSA는 Kubernetes ServiceAccount에 IAM Role을 연결해 Pod 단위로 AWS 권한을 부여하는 메커니즘이다.
EC2 인스턴스 프로파일처럼 노드 전체가 아닌 특정 Pod/ServiceAccount에만 최소 권한을 부여할 수 있어
EKS 보안 아키텍처의 핵심이다.

---

## 2. 설명

### 2.1 핵심 개념

**동작 원리**

```
Pod 시작
    │
    ├─ ServiceAccount에 annotation으로 IAM Role ARN 지정
    │      eks.amazonaws.com/role-arn: arn:aws:iam::123456789:role/my-role
    │
    ├─ EKS가 OIDC Token을 Pod에 자동 마운트
    │      /var/run/secrets/eks.amazonaws.com/serviceaccount/token
    │
    ├─ AWS SDK가 AssumeRoleWithWebIdentity 호출
    │      STS에 OIDC Token 제출 → 임시 자격 증명 발급
    │
    └─ Pod가 임시 자격 증명으로 AWS API 호출
```

**EC2 인스턴스 프로파일과의 차이**

| 항목 | 인스턴스 프로파일 | IRSA |
|------|-----------------|------|
| 권한 범위 | 노드 전체 | Pod / ServiceAccount 단위 |
| 권한 격리 | 불가 | 가능 |
| 자격 증명 갱신 | 자동 | 자동 (1시간) |
| 감사 추적 | 노드 단위 | Pod/SA 단위 |
| 최소 권한 | 어려움 | 용이 |

---

### 2.2 실무 적용 코드

**Terraform — OIDC Provider 생성**

```hcl
# EKS 클러스터의 OIDC Issuer URL로 Provider 생성
data "tls_certificate" "eks" {
  url = module.eks.cluster_oidc_issuer_url
}

resource "aws_iam_openid_connect_provider" "eks" {
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.eks.certificates[0].sha1_fingerprint]
  url             = module.eks.cluster_oidc_issuer_url
}
```

**Terraform — IAM Role + Trust Policy (특정 SA만 허용)**

```hcl
locals {
  oidc_provider_arn = aws_iam_openid_connect_provider.eks.arn
  oidc_provider_url = replace(aws_iam_openid_connect_provider.eks.url, "https://", "")
}

resource "aws_iam_role" "s3_reader" {
  name = "eks-s3-reader"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Federated = local.oidc_provider_arn
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          # 특정 namespace의 특정 ServiceAccount만 허용
          "${local.oidc_provider_url}:sub" = "system:serviceaccount:production:s3-reader-sa"
          "${local.oidc_provider_url}:aud" = "sts.amazonaws.com"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "s3_reader" {
  role       = aws_iam_role.s3_reader.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess"
}
```

**Kubernetes — ServiceAccount + Deployment 설정**

```yaml
# ServiceAccount에 IAM Role annotation
apiVersion: v1
kind: ServiceAccount
metadata:
  name: s3-reader-sa
  namespace: production
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::123456789012:role/eks-s3-reader
---
# Deployment에서 ServiceAccount 지정
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-app
  namespace: production
spec:
  template:
    spec:
      serviceAccountName: s3-reader-sa   # IRSA 적용
      containers:
        - name: app
          image: my-app:latest
          # AWS SDK는 자동으로 OIDC Token 감지하여 AssumeRoleWithWebIdentity 호출
```

**실제 사용 예시 — aws-load-balancer-controller**

```hcl
module "aws_load_balancer_controller_irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.0"

  role_name                              = "aws-load-balancer-controller"
  attach_load_balancer_controller_policy = true

  oidc_providers = {
    main = {
      provider_arn               = aws_iam_openid_connect_provider.eks.arn
      namespace_service_accounts = ["kube-system:aws-load-balancer-controller"]
    }
  }
}
```

**IRSA 동작 확인 (Pod 내부)**

```bash
# Pod 내부에서 OIDC Token 마운트 확인
ls /var/run/secrets/eks.amazonaws.com/serviceaccount/

# 현재 자격 증명 확인 (AssumedRole인지 확인)
aws sts get-caller-identity
# {
#   "UserId": "AROA...:botocore-session-xxx",
#   "Account": "123456789012",
#   "Arn": "arn:aws:sts::123456789012:assumed-role/eks-s3-reader/botocore-session-xxx"
# }
```

---

### 2.3 보안/비용 Best Practice

- **Trust Policy에 반드시 namespace:serviceaccount 조건 지정**: 와일드카드(`*`) 사용 시 다른 SA도 해당 Role 가정 가능
- **ServiceAccount 1:1 매핑**: 하나의 ServiceAccount에 하나의 IAM Role만 연결
- **IAM Role에 최소 권한**: 실제 사용하는 API 액션만 허용 (`s3:GetObject`만 필요하면 `AmazonS3ReadOnlyAccess` 대신 커스텀 정책)
- **자격 증명 캐싱**: AWS SDK가 자동으로 토큰을 갱신하므로 별도 처리 불필요

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**AssumeRoleWithWebIdentity 실패**

```bash
# Pod 내부에서 오류 확인
aws sts get-caller-identity
# An error occurred (AccessDenied) when calling the AssumeRoleWithWebIdentity operation

# 원인 1: Trust Policy의 sub 조건과 실제 SA 불일치
# Trust Policy: system:serviceaccount:production:my-sa
# 실제 SA namespace: staging (불일치)

# Trust Policy 확인
aws iam get-role --role-name my-role \
  --query 'Role.AssumeRolePolicyDocument'

# 원인 2: ServiceAccount annotation 오타
kubectl describe sa my-sa -n production | grep eks.amazonaws.com
```

**OIDC Token이 마운트되지 않음**

```bash
# Pod에 projected volume 확인
kubectl describe pod my-pod -n production | grep -A 20 Volumes

# ServiceAccount에 annotation이 있는지 확인
kubectl get sa my-sa -n production -o yaml | grep role-arn

# Pod의 serviceAccountName 확인
kubectl get pod my-pod -n production -o yaml | grep serviceAccountName
```

**SDK가 OIDC Token을 감지하지 못함**

```bash
# 환경변수 확인 (EKS에서 자동 주입)
env | grep AWS_WEB_IDENTITY_TOKEN_FILE
env | grep AWS_ROLE_ARN

# AWS SDK 버전이 너무 오래된 경우 (IRSA 미지원)
# boto3 >= 1.9.220, aws-sdk-java >= 1.11.704 필요
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: 여러 namespace에서 같은 IAM Role을 사용하고 싶어요**
A: Trust Policy의 Condition을 배열로 확장하거나 StringLike 와일드카드를 사용합니다.
```json
"Condition": {
  "StringLike": {
    "oidc.eks.ap-northeast-2.amazonaws.com/id/xxx:sub":
      "system:serviceaccount:*:my-service-sa"
  }
}
```

**Q: Pod를 재시작했더니 권한 오류가 발생합니다**
A: 임시 자격 증명 만료 가능성이 있습니다. AWS SDK 버전을 업데이트하면 자동 갱신이 됩니다. 또는 `AWS_METADATA_SERVICE_TIMEOUT` 환경변수를 늘려 STS 호출 타임아웃을 조정하세요.

---

## 4. 모니터링 및 알람

```hcl
# CloudTrail — AssumeRoleWithWebIdentity 이벤트 추적
resource "aws_cloudwatch_event_rule" "irsa_assume_role" {
  name = "irsa-assume-role-failed"

  event_pattern = jsonencode({
    source      = ["aws.sts"]
    detail-type = ["AWS API Call via CloudTrail"]
    detail = {
      eventSource = ["sts.amazonaws.com"]
      eventName   = ["AssumeRoleWithWebIdentity"]
      errorCode   = ["AccessDenied"]
    }
  })
}
```

**CloudTrail에서 IRSA 관련 이벤트**

```bash
# AssumeRoleWithWebIdentity 성공 이벤트 조회 (Athena)
SELECT eventTime, userIdentity.principalId, requestParameters.roleArn
FROM cloudtrail_logs
WHERE eventName = 'AssumeRoleWithWebIdentity'
  AND eventTime > '2024-01-01'
ORDER BY eventTime DESC
LIMIT 100;
```

---

## 5. TIP

- **terraform-aws-modules/iam 모듈** 활용: `iam-role-for-service-accounts-eks` 서브모듈이 Trust Policy 자동 생성 + 주요 AWS 서비스별 IAM Policy 사전 정의 (EBS CSI, LBC, External DNS 등)
- **IAM Access Analyzer**: IRSA Role에 대해 실제 사용되지 않는 권한 탐지 가능
- **eksctl로 IRSA 빠르게 생성**:
  ```bash
  eksctl create iamserviceaccount \
    --cluster my-cluster \
    --namespace production \
    --name my-sa \
    --attach-policy-arn arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess \
    --approve
  ```
