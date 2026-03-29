# AWS Organizations 멀티 계정 전략

## 1. 개요

AWS Organizations는 여러 AWS 계정을 중앙에서 관리하고 정책을 일괄 적용하는 서비스다.
단일 계정 구조는 환경 격리, 비용 추적, 보안 경계가 불명확해지므로
프로덕션/개발/보안/네트워크 등 목적별 계정을 분리하는 멀티 계정 전략이 엔터프라이즈 표준이다.
SCP(Service Control Policy)로 전체 계정에 보안 가드레일을 일관되게 적용할 수 있다.

---

## 2. 설명

### 2.1 핵심 개념

**계정 분리의 핵심 이점**

```
1. 폭발 반경(Blast Radius) 제한: 한 계정 침해 → 다른 계정 격리
2. 비용 투명성: 계정별 Cost Explorer로 팀/서비스별 비용 추적
3. 서비스 한도 분리: EC2 인스턴스 수 등 한도가 계정별 독립
4. 규정 준수: 보안 계정에 감사 로그 집중, 개발팀은 접근 불가
```

**권장 OU(Organizational Unit) 구조**

```
Root
├── Management (관리 계정 — Organizations 관리만, 워크로드 없음)
├── Security OU
│   ├── Security Tooling (GuardDuty, Security Hub, Config 집중)
│   └── Log Archive (CloudTrail, Flow Logs 중앙 저장)
├── Infrastructure OU
│   ├── Network (Transit Gateway, Direct Connect, DNS)
│   └── Shared Services (ECR, Artifactory, 내부 도구)
├── Workloads OU
│   ├── Production OU
│   │   ├── Prod-App-A
│   │   └── Prod-App-B
│   └── NonProd OU
│       ├── Staging-App-A
│       └── Dev-App-A
└── Sandbox OU
    └── 개발자 실험용 계정 (SCP로 비용 상한 적용)
```

**계정당 역할 요약**

| 계정 | 역할 | 주요 제약 |
|------|------|---------|
| Management | Organizations 관리, 통합 결제 | 워크로드 배포 금지 |
| Log Archive | 감사 로그 수집 | 로그 삭제/수정 금지 (SCP) |
| Security Tooling | GuardDuty Master, Security Hub | 보안팀만 접근 |
| Network | TGW, DX, VPN, Route 53 | 네트워크팀만 변경 가능 |
| Prod 계정들 | 실제 서비스 운영 | 개발팀 직접 접근 제한 |
| Sandbox | 자유로운 실험 | 월 비용 상한 (Budget + SCP) |

---

### 2.2 실무 적용 코드

**Terraform — Organizations 기본 설정**

```hcl
# Organizations 활성화 (Management 계정에서만)
resource "aws_organizations_organization" "main" {
  aws_service_access_principals = [
    "cloudtrail.amazonaws.com",
    "config.amazonaws.com",
    "guardduty.amazonaws.com",
    "securityhub.amazonaws.com",
    "sso.amazonaws.com",
    "ram.amazonaws.com",
    "account.amazonaws.com"
  ]

  feature_set = "ALL"   # SCP 사용을 위해 ALL 필수
  enabled_policy_types = ["SERVICE_CONTROL_POLICY", "TAG_POLICY"]
}

# OU 생성
resource "aws_organizations_organizational_unit" "security" {
  name      = "Security"
  parent_id = aws_organizations_organization.main.roots[0].id
}

resource "aws_organizations_organizational_unit" "workloads_prod" {
  name      = "Production"
  parent_id = aws_organizations_organizational_unit.workloads.id
}

# 새 계정 생성
resource "aws_organizations_account" "prod_app_a" {
  name      = "prod-app-a"
  email     = "aws+prod-app-a@mycompany.com"
  parent_id = aws_organizations_organizational_unit.workloads_prod.id

  # 계정 생성 시 기본 Role (다른 계정에서 Assume 가능)
  role_name = "OrganizationAccountAccessRole"

  tags = {
    Environment = "production"
    Team        = "app-a-team"
    CostCenter  = "CC-001"
  }
}
```

**SCP — 핵심 보안 가드레일**

```hcl
# 1. CloudTrail 비활성화 금지
resource "aws_organizations_policy" "deny_cloudtrail_stop" {
  name = "DenyCloudTrailStop"
  type = "SERVICE_CONTROL_POLICY"

  content = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "DenyCloudTrailModification"
      Effect = "Deny"
      Action = [
        "cloudtrail:StopLogging",
        "cloudtrail:DeleteTrail",
        "cloudtrail:UpdateTrail"
      ]
      Resource = "*"
    }]
  })
}

# 2. 허용된 리전만 사용 (리전 제한)
resource "aws_organizations_policy" "allow_regions" {
  name = "AllowSpecificRegions"
  type = "SERVICE_CONTROL_POLICY"

  content = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "DenyNonApprovedRegions"
      Effect = "Deny"
      NotAction = [
        # 글로벌 서비스는 제외
        "iam:*", "organizations:*", "support:*",
        "cloudfront:*", "route53:*", "sts:*"
      ]
      Resource = "*"
      Condition = {
        StringNotEquals = {
          "aws:RequestedRegion" = ["ap-northeast-2", "us-east-1"]
        }
      }
    }]
  })
}

# 3. Root 계정 사용 차단
resource "aws_organizations_policy" "deny_root" {
  name = "DenyRootAccountActions"
  type = "SERVICE_CONTROL_POLICY"

  content = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "DenyRootAccount"
      Effect = "Deny"
      Action = "*"
      Resource = "*"
      Condition = {
        StringLike = {
          "aws:PrincipalArn" = ["arn:aws:iam::*:root"]
        }
      }
    }]
  })
}

# SCP를 OU에 연결
resource "aws_organizations_policy_attachment" "deny_cloudtrail" {
  policy_id = aws_organizations_policy.deny_cloudtrail_stop.id
  target_id = aws_organizations_organization.main.roots[0].id   # Root 전체 적용
}
```

**IAM Identity Center (SSO) — 중앙 접근 관리**

```hcl
# SSO Permission Set 생성 (개발자용)
resource "aws_ssoadmin_permission_set" "developer" {
  name             = "DeveloperAccess"
  instance_arn     = tolist(data.aws_ssoadmin_instances.main.arns)[0]
  session_duration = "PT8H"   # 8시간 세션

  # Permission Boundary 적용 (권한 에스컬레이션 방지)
  permissions_boundary {
    managed_policy_arn = aws_iam_policy.developer_boundary.arn
  }
}

resource "aws_ssoadmin_managed_policy_attachment" "developer" {
  instance_arn       = tolist(data.aws_ssoadmin_instances.main.arns)[0]
  permission_set_arn = aws_ssoadmin_permission_set.developer.arn
  managed_policy_arn = "arn:aws:iam::aws:policy/PowerUserAccess"
}

# 계정에 Permission Set 할당
resource "aws_ssoadmin_account_assignment" "developer_to_dev" {
  instance_arn       = tolist(data.aws_ssoadmin_instances.main.arns)[0]
  permission_set_arn = aws_ssoadmin_permission_set.developer.arn

  principal_id   = data.aws_identitystore_group.developers.group_id
  principal_type = "GROUP"

  target_id   = aws_organizations_account.dev_app_a.id
  target_type = "AWS_ACCOUNT"
}
```

**크로스 계정 Role Assume (자동화 파이프라인)**

```hcl
# 각 계정에 공통 배포 Role 생성 (피호출 계정)
resource "aws_iam_role" "deploy" {
  name = "DeployRole"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        AWS = "arn:aws:iam::CICD_ACCOUNT_ID:role/CICDRole"
      }
      Action = "sts:AssumeRole"
      Condition = {
        StringEquals = {
          "sts:ExternalId" = "deploy-secret-id"   # 혼동 대리인 방지
        }
      }
    }]
  })
}
```

---

### 2.3 보안/비용 Best Practice

- **Management 계정에 워크로드 배포 금지**: Management 계정 침해 시 전체 Organization 제어 가능. SCP로 EC2/ECS 등 배포 차단
- **Tag Policy로 비용 추적 강제**: Organizations Tag Policy로 `Environment`, `Team`, `CostCenter` 태그 미부착 리소스 탐지
- **Control Tower 활용**: 멀티 계정 설정 자동화 (Log Archive, Security Tooling 계정 자동 생성, Guardrails 자동 적용)
- **계정당 Budget 알람**: 각 계정에 월 예산 설정 + SNS 알람. Sandbox 계정은 SCP로 예산 초과 시 배포 차단 가능

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**SCP 적용 후 관리자도 접근 불가**

```bash
# SCP는 Management 계정의 root를 제외한 모든 계정에 적용
# SCP Deny는 AdministratorAccess도 차단

# 현재 계정에 적용된 SCP 확인
aws organizations list-policies-for-target \
  --target-id $(aws sts get-caller-identity --query Account --output text) \
  --filter SERVICE_CONTROL_POLICY \
  --query 'Policies[*].{Name:Name,Id:Id}'

# 특정 액션이 어떤 SCP에서 차단되는지 확인
aws iam simulate-principal-policy \
  --policy-source-arn arn:aws:iam::123456789012:role/AdminRole \
  --action-names "cloudtrail:StopLogging" \
  --resource-arns "*"
```

**새 계정 생성 후 접근 방법**

```bash
# 계정 생성 시 OrganizationAccountAccessRole이 자동 생성됨
# Management 계정에서 해당 Role Assume

aws sts assume-role \
  --role-arn arn:aws:iam::NEW_ACCOUNT_ID:role/OrganizationAccountAccessRole \
  --role-session-name initial-setup
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: 기존 단일 계정 워크로드를 멀티 계정으로 마이그레이션하는 순서는?**
A: ① Organizations 활성화 → ② 새 계정 생성 (Prod/Staging/Dev) → ③ SCP 기본 가드레일 적용 → ④ TGW/VPC 네트워크 연결 → ⑤ IAM Identity Center SSO 설정 → ⑥ 서비스별 점진적 마이그레이션. 한 번에 이전하지 말고 서비스 단위로 분리하세요.

**Q: 계정당 IAM 사용자를 따로 만들어야 하나요?**
A: 아닙니다. IAM Identity Center(SSO)를 쓰면 중앙 IdP에서 한 번 로그인 후 여러 계정에 권한별로 접근 가능합니다. 계정별 IAM 사용자 생성은 관리 부담이 크고 보안 위험이 높습니다.

---

## 4. 모니터링 및 알람

```hcl
# 새 계정 생성 감지 (Management 계정)
resource "aws_cloudwatch_event_rule" "new_account" {
  name = "organizations-new-account"

  event_pattern = jsonencode({
    source      = ["aws.organizations"]
    detail-type = ["AWS API Call via CloudTrail"]
    detail = {
      eventName = ["CreateAccount", "InviteAccountToOrganization"]
    }
  })
}

# SCP 변경 감지
resource "aws_cloudwatch_event_rule" "scp_change" {
  name = "scp-policy-change"

  event_pattern = jsonencode({
    source      = ["aws.organizations"]
    detail-type = ["AWS API Call via CloudTrail"]
    detail = {
      eventName = ["CreatePolicy", "UpdatePolicy", "DeletePolicy",
                   "AttachPolicy", "DetachPolicy"]
    }
  })
}
```

---

## 5. TIP

- **AWS Control Tower**: 멀티 계정 설정을 자동화. 계정 팩토리(Account Factory)로 표준 계정을 템플릿 기반으로 프로비저닝. 직접 구성보다 빠르게 시작 가능
- **IP 주소 공간 계획**: 멀티 계정 환경에서는 계정별 VPC CIDR이 겹치면 TGW 연결 불가. 처음부터 계정별 /16 블록을 엑셀로 관리 (`vpc-subnet-design.md` 참고)
- **AWS Config Aggregator**: 모든 계정의 Config 규정 준수 상태를 Security Tooling 계정 하나에서 집계 조회 가능
- **Cost Allocation Tag 강제**: Organizations에서 Tag Policy를 루트에 적용하면 미준수 리소스를 자동으로 탐지해 비용 추적 공백을 방지
