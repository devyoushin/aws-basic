# IAM Roles for Service Accounts (IRSA)

## 1. 개요

IRSA는 Kubernetes ServiceAccount에 IAM Role을 연결해 Pod 단위로 AWS 권한을 부여하는 메커니즘이다.
EC2 인스턴스 프로파일처럼 노드 전체가 아닌 특정 Pod/ServiceAccount에만 최소 권한을 부여할 수 있어
EKS 보안 아키텍처의 핵심이다.

---

## 2. 설명

### 2.1 핵심 개념

#### IRSA 전체 플로우 (단계별 상세)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         IRSA 토큰 교환 전체 플로우                            │
└─────────────────────────────────────────────────────────────────────────────┘

[사전 설정]
1. EKS 클러스터 생성 시 OIDC Issuer URL 자동 부여
   https://oidc.eks.ap-northeast-2.amazonaws.com/id/<CLUSTER_ID>

2. IAM에 OIDC Provider 등록
   → EKS의 OIDC Issuer URL을 신뢰할 수 있는 IdP로 IAM에 등록

3. IAM Role Trust Policy에 OIDC Provider + SA 조건 지정
   → "이 OIDC Provider가 서명한 토큰이고, sub=system:serviceaccount:NS:SA 인 경우만 허용"

[런타임 — Pod 시작 시]
  Pod 생성 요청
      │
      ▼
  ┌──────────────────────────┐
  │  Kubernetes API Server   │  ← ServiceAccount annotation에 role-arn 있음을 감지
  │  (EKS Control Plane)     │
  └──────────┬───────────────┘
             │ Projected ServiceAccount Token 생성
             │ (TokenRequest API — Kubernetes 1.20+)
             │
             │ JWT 페이로드 예시:
             │ {
             │   "iss": "https://oidc.eks.ap-northeast-2.amazonaws.com/id/XXXXX",
             │   "sub": "system:serviceaccount:production:s3-reader-sa",
             │   "aud": ["sts.amazonaws.com"],
             │   "exp": 1234567890,    ← 기본 24시간 (EKS 기본값)
             │   "iat": 1234481490,
             │   "kubernetes.io": {
             │     "namespace": "production",
             │     "serviceaccount": { "name": "s3-reader-sa", ... },
             │     "pod": { "name": "my-app-xxx", ... }
             │   }
             │ }
             │
             ▼
  ┌──────────────────────────┐
  │  Pod (컨테이너 내부)       │
  │                          │
  │  마운트 경로:              │
  │  /var/run/secrets/        │
  │    eks.amazonaws.com/     │
  │    serviceaccount/token   │
  │                          │
  │  환경변수 자동 주입:        │
  │  AWS_WEB_IDENTITY_TOKEN_FILE │
  │  = /var/run/secrets/...   │
  │  AWS_ROLE_ARN             │
  │  = arn:aws:iam::...:role/ │
  └──────────┬───────────────┘
             │
             │ AWS SDK가 환경변수 감지 → AssumeRoleWithWebIdentity 자동 호출
             ▼
  ┌──────────────────────────┐     ┌────────────────────────────┐
  │  AWS STS                 │ ←── │  JWT Token (Bearer)        │
  │  AssumeRoleWithWebIdentity    │     │  + RoleArn                 │
  │                          │     │  + RoleSessionName         │
  │  검증 과정:               │     └────────────────────────────┘
  │  1. JWT iss 클레임에서
  │     OIDC Provider URL 추출
  │  2. OIDC Provider의
  │     /.well-known/jwks.json 에서
  │     공개키 가져옴
  │  3. JWT 서명 검증
  │  4. aud, sub 클레임이
  │     Trust Policy Condition과 일치하는지 확인
  │  5. 토큰 만료(exp) 확인
  └──────────┬───────────────┘
             │ 임시 자격 증명 발급 (기본 1시간)
             │ AccessKeyId / SecretAccessKey / SessionToken
             ▼
  ┌──────────────────────────┐
  │  Pod (AWS SDK)           │  → S3, DynamoDB, SQS 등 AWS API 호출
  │                          │
  │  자격 증명 캐싱 & 자동 갱신 │
  │  (만료 5분 전 SDK가 재호출) │
  └──────────────────────────┘
```

#### Projected ServiceAccount Token 메커니즘 상세

기존 Kubernetes ServiceAccount Token(Secret 기반)과 달리 **Projected Volume** 방식은:

| 항목 | 기존 Secret 토큰 | Projected Token (IRSA) |
|------|----------------|------------------------|
| 만료 | 없음 (영구) | 있음 (기본 24h, EKS는 86400s) |
| audience | kubernetes.default.svc | sts.amazonaws.com |
| 갱신 | 수동 | kubelet이 자동 갱신 |
| 저장 | etcd (Secret) | kubelet이 메모리에서 생성 |
| 보안 | 탈취 시 영구 유효 | 탈취해도 단시간 내 만료 |

```yaml
# EKS가 Pod에 자동 주입하는 Projected Volume (직접 작성 불필요)
volumes:
  - name: aws-iam-token
    projected:
      sources:
        - serviceAccountToken:
            audience: sts.amazonaws.com
            expirationSeconds: 86400
            path: token
```

#### STS AssumeRoleWithWebIdentity 검증 흐름

```
STS 검증 단계:

① JWT Header에서 kid(Key ID) 추출
② JWT iss 클레임 → OIDC Provider URL 확인
   → IAM에 등록된 OIDC Provider인지 검증
③ https://{oidc_issuer}/.well-known/openid-configuration 에서
   jwks_uri 조회 → 공개키 목록(JWKS) 가져옴
④ kid에 해당하는 공개키로 JWT 서명 검증
⑤ Trust Policy Condition 매칭:
   - sub: "system:serviceaccount:NAMESPACE:SA_NAME"
   - aud: "sts.amazonaws.com"
⑥ 임시 자격 증명 발급 (AssumedRole 세션)
```

#### EC2 인스턴스 프로파일과의 차이

| 항목 | 인스턴스 프로파일 | IRSA |
|------|-----------------|------|
| 권한 범위 | 노드 전체 | Pod / ServiceAccount 단위 |
| 권한 격리 | 불가 | 가능 |
| 자격 증명 갱신 | 자동 | 자동 (SDK가 만료 전 재호출) |
| 감사 추적 | 노드 단위 | Pod/SA 단위 (CloudTrail에 SA명 기록) |
| 최소 권한 | 어려움 | 용이 |
| 자격 증명 유출 시 | 노드 전체 영향 | 해당 Role만 영향, 단시간 내 만료 |

#### IRSA vs EKS Pod Identity (2023년 신규)

AWS는 2023년 re:Invent에서 **EKS Pod Identity**를 발표했다.
IRSA의 대안으로 설정이 더 간편하지만, 현재 프로덕션에는 IRSA가 더 넓게 사용된다.

| 항목 | IRSA | EKS Pod Identity |
|------|------|-----------------|
| 설정 위치 | SA annotation + IAM Trust Policy | EKS Pod Identity Association (콘솔/API) |
| OIDC Provider 관리 | 직접 생성/관리 필요 | EKS가 자동 관리 |
| Cross-account | 가능 | 가능 |
| 지원 환경 | 모든 EKS 버전 | EKS 1.24+ (Agent 설치 필요) |
| 감사 추적 | CloudTrail AssumeRoleWithWebIdentity | CloudTrail AssumeRoleForPodIdentity |
| 권장 신규 구성 | 기존 환경 유지 | 신규 클러스터 권장 |

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
          # 특정 namespace의 특정 ServiceAccount만 허용 (가장 엄격한 설정)
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

**Terraform — 여러 SA를 같은 Role에 허용 (멀티 namespace)**

```hcl
# StringLike로 여러 namespace 허용
assume_role_policy = jsonencode({
  Version = "2012-10-17"
  Statement = [
    {
      Effect    = "Allow"
      Principal = { Federated = local.oidc_provider_arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringLike = {
          "${local.oidc_provider_url}:sub" = [
            "system:serviceaccount:production:app-sa",
            "system:serviceaccount:staging:app-sa"
          ]
          "${local.oidc_provider_url}:aud" = "sts.amazonaws.com"
        }
      }
    }
  ]
})
```

**Terraform — Cross-Account IRSA (Account A의 EKS → Account B의 Role)**

```hcl
# Account B의 IAM Role Trust Policy
# Account A의 OIDC Provider ARN을 신뢰
resource "aws_iam_role" "cross_account_role" {
  provider = aws.account_b
  name     = "cross-account-eks-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        # Account A의 OIDC Provider ARN
        Federated = "arn:aws:iam::111111111111:oidc-provider/oidc.eks.ap-northeast-2.amazonaws.com/id/XXXXX"
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "oidc.eks.ap-northeast-2.amazonaws.com/id/XXXXX:sub" = "system:serviceaccount:production:cross-account-sa"
          "oidc.eks.ap-northeast-2.amazonaws.com/id/XXXXX:aud" = "sts.amazonaws.com"
        }
      }
    }]
  })
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
    # 토큰 만료 시간 커스텀 (기본 86400s = 24h, 최소 3600s)
    eks.amazonaws.com/token-expiration: "3600"
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
          # 환경변수 수동 확인용 (자동 주입되므로 직접 지정 불필요)
          # env:
          #   - name: AWS_WEB_IDENTITY_TOKEN_FILE
          #     value: /var/run/secrets/eks.amazonaws.com/serviceaccount/token
          #   - name: AWS_ROLE_ARN
          #     value: arn:aws:iam::123456789012:role/eks-s3-reader
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
# token

# JWT 토큰 디코딩 (base64로 페이로드 확인)
TOKEN=$(cat /var/run/secrets/eks.amazonaws.com/serviceaccount/token)
echo $TOKEN | cut -d. -f2 | base64 -d 2>/dev/null | python3 -m json.tool
# {
#   "iss": "https://oidc.eks.ap-northeast-2.amazonaws.com/id/XXXXX",
#   "sub": "system:serviceaccount:production:s3-reader-sa",
#   "aud": ["sts.amazonaws.com"],
#   ...
# }

# 환경변수 확인 (EKS에서 자동 주입)
env | grep AWS
# AWS_WEB_IDENTITY_TOKEN_FILE=/var/run/secrets/eks.amazonaws.com/serviceaccount/token
# AWS_ROLE_ARN=arn:aws:iam::123456789012:role/eks-s3-reader
# AWS_DEFAULT_REGION=ap-northeast-2

# 현재 자격 증명 확인 (AssumedRole인지 확인)
aws sts get-caller-identity
# {
#   "UserId": "AROA...:botocore-session-xxx",
#   "Account": "123456789012",
#   "Arn": "arn:aws:sts::123456789012:assumed-role/eks-s3-reader/botocore-session-xxx"
# }

# 토큰 만료 시간 확인
TOKEN=$(cat /var/run/secrets/eks.amazonaws.com/serviceaccount/token)
echo $TOKEN | cut -d. -f2 | base64 -d 2>/dev/null | python3 -c "
import json, sys, datetime
d = json.load(sys.stdin)
print('exp:', datetime.datetime.fromtimestamp(d['exp']))
print('iat:', datetime.datetime.fromtimestamp(d['iat']))
"
```

---

### 2.3 보안/비용 Best Practice

- **Trust Policy에 반드시 `namespace:serviceaccount` 조건 지정**: 와일드카드(`*`) 사용 시 다른 SA도 해당 Role 가정 가능
- **`aud` 조건 명시**: `sts.amazonaws.com` 고정 — 다른 audience로 발급된 토큰 차단
- **ServiceAccount 1:1 매핑**: 하나의 ServiceAccount에 하나의 IAM Role만 연결
- **IAM Role에 최소 권한**: 실제 사용하는 API 액션만 허용
- **토큰 만료 시간 단축**: 민감한 워크로드는 `eks.amazonaws.com/token-expiration: "3600"` (1시간)
- **automountServiceAccountToken: false**: IRSA 미사용 Pod에는 토큰 마운트 비활성화

```yaml
# IRSA 불필요한 Pod는 토큰 마운트 비활성화
apiVersion: v1
kind: ServiceAccount
metadata:
  name: no-aws-access-sa
  namespace: production
automountServiceAccountToken: false
```

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

# 원인 3: OIDC Provider thumbprint 불일치 (드물게 발생)
# → OIDC Provider 삭제 후 재생성
aws iam list-open-id-connect-providers
aws iam get-open-id-connect-provider \
  --open-id-connect-provider-arn arn:aws:iam::123456789012:oidc-provider/...
```

**OIDC Token이 마운트되지 않음**

```bash
# Pod에 projected volume 확인
kubectl describe pod my-pod -n production | grep -A 20 Volumes

# ServiceAccount에 annotation이 있는지 확인
kubectl get sa my-sa -n production -o yaml | grep role-arn

# Pod의 serviceAccountName 확인
kubectl get pod my-pod -n production -o yaml | grep serviceAccountName

# EKS 버전 확인 (1.13 미만은 IRSA 미지원)
kubectl version --short
```

**SDK가 OIDC Token을 감지하지 못함**

```bash
# 환경변수 확인 (EKS에서 자동 주입)
env | grep AWS_WEB_IDENTITY_TOKEN_FILE
env | grep AWS_ROLE_ARN

# AWS SDK 버전이 너무 오래된 경우 (IRSA 미지원)
# boto3 >= 1.9.220, aws-sdk-java >= 1.11.704 필요
# aws-sdk-go >= 1.23.13, aws-sdk-js >= 2.521.0 필요

# Node.js에서 AssumeRoleWithWebIdentity 직접 호출 테스트
node -e "
const { STSClient, AssumeRoleWithWebIdentityCommand } = require('@aws-sdk/client-sts');
const fs = require('fs');
const token = fs.readFileSync(process.env.AWS_WEB_IDENTITY_TOKEN_FILE, 'utf8');
const client = new STSClient({ region: 'ap-northeast-2' });
client.send(new AssumeRoleWithWebIdentityCommand({
  RoleArn: process.env.AWS_ROLE_ARN,
  RoleSessionName: 'test',
  WebIdentityToken: token
})).then(r => console.log(r.Credentials)).catch(console.error);
"
```

**자격 증명 갱신 실패 (토큰 파일은 있지만 STS 호출 실패)**

```bash
# STS 엔드포인트 접근 가능 여부 확인 (Private Endpoint 환경)
curl -v https://sts.amazonaws.com/

# Private STS Endpoint 설정 여부 확인
# VPC Endpoint for STS가 없으면 인터넷 경유 필요
aws ec2 describe-vpc-endpoints \
  --filters "Name=service-name,Values=com.amazonaws.ap-northeast-2.sts"

# 리전별 STS 엔드포인트 사용 (글로벌 STS 대신)
# AWS_STS_REGIONAL_ENDPOINTS=regional 환경변수 설정 권장
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

**Q: CloudTrail에서 어떤 Pod가 어떤 API를 호출했는지 확인하려면?**
A: CloudTrail의 `userIdentity.sessionContext.sessionIssuer.userName`에 Role 이름이 나타나고,
`userIdentity.principalId`에는 `AROA...:botocore-session-xxx` 형태로 세션 이름이 기록됩니다.
정확한 Pod 추적은 `eks.amazonaws.com/token-expiration`을 짧게 하고 Pod 이름을 세션 이름에 포함시키는 방법도 있습니다.

**Q: IRSA Role이 과도한 권한을 갖고 있는지 어떻게 점검하나요?**
A: IAM Access Analyzer의 "Unused Access" 기능을 사용하거나 아래 CLI로 확인합니다.
```bash
# 90일간 미사용 액션 탐지
aws iam generate-service-last-accessed-details \
  --arn arn:aws:iam::123456789012:role/eks-s3-reader

aws iam get-service-last-accessed-details \
  --job-id <job-id>
```

---

## 4. 모니터링 및 알람

```hcl
# CloudTrail — AssumeRoleWithWebIdentity AccessDenied 이벤트 알람
resource "aws_cloudwatch_event_rule" "irsa_assume_role_failed" {
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

resource "aws_cloudwatch_event_target" "irsa_alarm_sns" {
  rule      = aws_cloudwatch_event_rule.irsa_assume_role_failed.name
  target_id = "SendToSNS"
  arn       = aws_sns_topic.security_alerts.arn
}
```

**CloudTrail에서 IRSA 관련 이벤트 (Athena 쿼리)**

```sql
-- AssumeRoleWithWebIdentity 호출 현황 (성공/실패 포함)
SELECT
  eventTime,
  errorCode,
  userIdentity.principalId,
  requestParameters.roleArn,
  requestParameters.roleSessionName,
  sourceIpAddress
FROM cloudtrail_logs
WHERE eventName = 'AssumeRoleWithWebIdentity'
  AND eventTime > date_add('day', -7, current_date)
ORDER BY eventTime DESC
LIMIT 100;

-- IRSA Role별 호출 횟수 집계
SELECT
  requestParameters.roleArn,
  COUNT(*) as call_count,
  COUNT_IF(errorCode IS NOT NULL) as error_count
FROM cloudtrail_logs
WHERE eventName = 'AssumeRoleWithWebIdentity'
  AND eventTime > date_add('day', -1, current_date)
GROUP BY requestParameters.roleArn
ORDER BY call_count DESC;
```

---

## 5. TIP

- **terraform-aws-modules/iam 모듈** 활용: `iam-role-for-service-accounts-eks` 서브모듈이 Trust Policy 자동 생성 + 주요 AWS 서비스별 IAM Policy 사전 정의 (EBS CSI, LBC, External DNS 등)
- **IAM Access Analyzer**: IRSA Role에 대해 실제 사용되지 않는 권한 탐지 가능
- **리전별 STS 엔드포인트**: `AWS_STS_REGIONAL_ENDPOINTS=regional` 환경변수 설정으로 지연 감소 및 글로벌 STS 의존성 제거
- **eksctl로 IRSA 빠르게 생성**:
  ```bash
  eksctl create iamserviceaccount \
    --cluster my-cluster \
    --namespace production \
    --name my-sa \
    --attach-policy-arn arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess \
    --approve
  ```
- **OIDC Discovery Endpoint 직접 확인**:
  ```bash
  OIDC_URL=$(aws eks describe-cluster --name my-cluster \
    --query 'cluster.identity.oidc.issuer' --output text)

  # OIDC 메타데이터 확인
  curl -s ${OIDC_URL}/.well-known/openid-configuration | python3 -m json.tool

  # 공개키 목록 확인
  JWKS_URI=$(curl -s ${OIDC_URL}/.well-known/openid-configuration | python3 -c "import sys,json; print(json.load(sys.stdin)['jwks_uri'])")
  curl -s $JWKS_URI | python3 -m json.tool
  ```
