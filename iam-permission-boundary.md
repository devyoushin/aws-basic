# IAM Permission Boundary 설계 패턴

## 1. 개요

Permission Boundary는 IAM 엔티티(User/Role)가 가질 수 있는 **최대 권한의 상한선**을 설정하는 기능이다.
Identity Policy와 Permission Boundary의 교집합이 실제 유효 권한이 된다.
개발팀에 IAM Role 생성 권한을 위임하면서 권한 에스컬레이션을 방지할 때 핵심적으로 사용된다.

---

## 2. 설명

### 2.1 핵심 개념

**실제 유효 권한 결정 로직**

```
실제 유효 권한 = Identity Policy ∩ Permission Boundary ∩ (SCP if in Organizations)

예시:
  Identity Policy: S3 Full Access + IAM Full Access
  Permission Boundary: S3 Full Access only

  → 실제 권한: S3 Full Access만 (IAM 권한은 Boundary에 없으므로 제외)
```

**Permission Boundary vs SCP (Organizations) 차이**

| 항목 | Permission Boundary | SCP (Service Control Policy) |
|------|--------------------|-----------------------------|
| 적용 대상 | 개별 IAM User/Role | AWS 계정/OU 전체 |
| 설정 주체 | 해당 계정 관리자 | Organizations 관리 계정 |
| 용도 | 개인/역할 단위 권한 상한 | 계정 단위 최대 권한 |
| 범위 | 엔티티별 설정 필요 | 자동으로 하위 계정 전체 적용 |

**Permission Boundary가 필요한 핵심 시나리오**

```
시나리오: 개발팀에 IAM Role 생성 권한 위임

문제: 개발팀이 IAM Full Access를 가지면
      → 자신에게 AdministratorAccess를 가진 Role을 생성하고 assume 가능
      → 권한 에스컬레이션 발생

해결: Permission Boundary 강제 부착 조건 추가
      → 개발팀이 만드는 모든 Role에 미리 정의된 Boundary 자동 부착
      → 만들어진 Role은 Boundary 이상의 권한 절대 불가
```

---

### 2.2 실무 적용 코드

**Terraform — Permission Boundary 정책 생성**

```hcl
# 개발팀이 생성하는 Role/User의 최대 권한 상한선
resource "aws_iam_policy" "developer_boundary" {
  name        = "DeveloperPermissionBoundary"
  description = "개발팀이 생성하는 IAM 엔티티의 최대 권한 상한선"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # 허용: 개발에 필요한 서비스
      {
        Sid    = "AllowedServices"
        Effect = "Allow"
        Action = [
          "s3:*",
          "ec2:*",
          "lambda:*",
          "logs:*",
          "cloudwatch:*",
          "ecs:*",
          "ecr:*",
          "secretsmanager:GetSecretValue",
          "ssm:GetParameter*"
        ]
        Resource = "*"
      },
      # 명시적 거부: 절대로 허용하면 안 되는 작업
      {
        Sid    = "DenyDangerousActions"
        Effect = "Deny"
        Action = [
          "iam:CreateUser",
          "iam:AttachUserPolicy",
          "iam:CreateAccessKey",
          "organizations:*",
          "account:*"
        ]
        Resource = "*"
      },
      # 허용: Boundary가 부착된 Role만 생성 가능 (에스컬레이션 방지 핵심)
      {
        Sid    = "AllowIAMWithBoundary"
        Effect = "Allow"
        Action = [
          "iam:CreateRole",
          "iam:PutRolePolicy",
          "iam:AttachRolePolicy"
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            # 생성하는 Role에 반드시 이 Boundary를 부착하도록 강제
            "iam:PermissionsBoundary" = "arn:aws:iam::${var.account_id}:policy/DeveloperPermissionBoundary"
          }
        }
      }
    ]
  })
}
```

**Terraform — 개발팀 IAM Role에 Boundary 적용**

```hcl
# 개발팀 Role 생성 시 Permission Boundary 부착
resource "aws_iam_role" "developer" {
  name                 = "developer-role"
  permissions_boundary = aws_iam_policy.developer_boundary.arn   # Boundary 부착

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { AWS = "arn:aws:iam::${var.account_id}:root" }
      Action    = "sts:AssumeRole"
      Condition = {
        Bool = {
          "aws:MultiFactorAuthPresent" = "true"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "developer_s3" {
  role       = aws_iam_role.developer.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonS3FullAccess"
}

# 실제 유효 권한 = S3FullAccess ∩ DeveloperPermissionBoundary = S3FullAccess
# (Boundary에 S3가 포함되어 있으므로)
```

**SCP로 Permission Boundary 강제 부착 (Organizations)**

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "RequirePermissionBoundaryOnNewRoles",
      "Effect": "Deny",
      "Action": [
        "iam:CreateRole",
        "iam:PutRolePermissionsBoundary"
      ],
      "Resource": "*",
      "Condition": {
        "StringNotEquals": {
          "iam:PermissionsBoundary": "arn:aws:iam::*:policy/StandardPermissionBoundary"
        }
      }
    },
    {
      "Sid": "DenyBoundaryRemoval",
      "Effect": "Deny",
      "Action": [
        "iam:DeleteRolePermissionsBoundary"
      ],
      "Resource": "*"
    }
  ]
}
```

**Permission Boundary 동작 확인 (Policy Simulator)**

```bash
# AWS Policy Simulator CLI로 Boundary 효과 검증
aws iam simulate-principal-policy \
  --policy-source-arn arn:aws:iam::123456789012:role/developer-role \
  --action-names "s3:GetObject" "iam:CreateRole" \
  --resource-arns "arn:aws:s3:::my-bucket/*" "arn:aws:iam::123456789012:role/*" \
  --query 'EvaluationResults[*].{Action:EvalActionName,Decision:EvalDecision}'

# 예상 결과:
# s3:GetObject → allowed
# iam:CreateRole → implicitDeny (Boundary에 조건 없이 허용 안 됨)
```

**현재 Role에 적용된 Boundary 확인**

```bash
aws iam get-role \
  --role-name developer-role \
  --query 'Role.PermissionsBoundary'

# 모든 Role 중 Boundary 없는 것 탐지
aws iam list-roles --query \
  'Roles[?PermissionsBoundary==null].{Name:RoleName,ARN:Arn}' \
  --output table
```

---

### 2.3 보안/비용 Best Practice

- **Boundary 삭제 금지 SCP 추가**: 개발팀이 Boundary를 스스로 제거하지 못하도록 SCP로 차단
- **Boundary 정책도 버전 관리**: 변경 시 CloudTrail 추적 + 변경 이유 태그 기록
- **최소 필요 서비스만 Boundary에 포함**: Deny가 아닌 Allow 방식으로 화이트리스트 관리
- **IAM Access Analyzer**: Boundary 적용 후 실제 과도한 권한 자동 탐지

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**Boundary 설정 후 예상 권한이 없음**

```bash
# 현상: S3 Full Access 정책이 있는데 S3 접근 거부

# 원인: Identity Policy와 Boundary 둘 다 해당 액션을 Allow해야 유효
# Identity Policy: S3:* Allow ✓
# Boundary: S3:* 없음 (또는 Deny) ✗ → 실제 권한 없음

# Policy Simulator로 원인 확인
aws iam simulate-principal-policy \
  --policy-source-arn arn:aws:iam::123456789012:role/my-role \
  --action-names "s3:PutObject" \
  --resource-arns "arn:aws:s3:::my-bucket/*"
```

**개발팀이 Boundary 없이 Role 생성 성공**

```bash
# IAM 정책의 Condition이 올바르게 설정되었는지 확인
aws iam get-policy-version \
  --policy-arn arn:aws:iam::123456789012:policy/DeveloperPolicy \
  --version-id v1 \
  --query 'PolicyVersion.Document'

# SCP가 적용된 OU에 계정이 포함되어 있는지 확인
aws organizations list-policies-for-target \
  --target-id 123456789012 \
  --filter SERVICE_CONTROL_POLICY
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: Boundary가 없는 AdministratorAccess Role은 모든 권한을 가지나요?**
A: Boundary 없이 AdministratorAccess가 붙은 Role은 해당 계정의 모든 권한을 가집니다. 단, SCP(Organizations)가 적용된 계정에서는 SCP가 상한선이 됩니다.

**Q: Permission Boundary를 나중에 추가하면 기존 작업에 영향이 있나요?**
A: 예. Boundary 추가 후 해당 Role이 수행하는 모든 API 호출은 즉시 재평가됩니다. Boundary에 없는 액션은 바로 AccessDenied가 됩니다. 반드시 스테이징에서 먼저 테스트하세요.

---

## 4. 모니터링 및 알람

```hcl
# Boundary 없는 Role 생성 감지
resource "aws_cloudwatch_event_rule" "role_without_boundary" {
  name = "iam-role-created-without-boundary"

  event_pattern = jsonencode({
    source      = ["aws.iam"]
    detail-type = ["AWS API Call via CloudTrail"]
    detail = {
      eventSource = ["iam.amazonaws.com"]
      eventName   = ["CreateRole"]
      requestParameters = {
        permissionsBoundary = [{ exists = false }]   # Boundary 없이 생성
      }
    }
  })
}

resource "aws_cloudwatch_event_target" "role_without_boundary_sns" {
  rule      = aws_cloudwatch_event_rule.role_without_boundary.name
  target_id = "AlertSNS"
  arn       = aws_sns_topic.security_alerts.arn
}
```

**IAM Access Analyzer 활용**

```bash
# Access Analyzer로 외부 접근 가능한 리소스 탐지
aws accessanalyzer list-findings \
  --analyzer-arn arn:aws:access-analyzer:ap-northeast-2:123456789012:analyzer/my-analyzer \
  --filter '{"status": {"eq": ["ACTIVE"]}}' \
  --query 'findings[*].{Resource:resource,Type:resourceType,Condition:condition}'
```

---

## 5. TIP

- **Boundary 적용 전 Policy Simulator 필수**: 예상치 못한 권한 차단 방지
- **Terraform으로 Boundary 생성 → Role 생성 → Attachment 순서**: `depends_on`으로 순서 보장
- **중앙집중식 Boundary 관리**: Boundary 정책을 별도 Terraform 모듈로 관리하고 모든 팀이 동일 버전 사용
- **AWS IAM Identity Center(SSO) 조합**: SSO Permission Set에도 Boundary를 적용하면 임시 자격 증명에도 상한선 적용 가능
