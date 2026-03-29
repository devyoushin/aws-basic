# ECR 이미지 관리 & Lifecycle 정책

## 1. 개요

ECR(Elastic Container Registry)에 이미지가 무기한 누적되면 스토리지 비용이 지속 증가하고,
오래된 취약한 이미지가 실수로 사용될 위험이 있다.
Lifecycle Policy로 오래된/미태그 이미지를 자동 삭제하고,
이미지 스캐닝으로 취약점을 배포 전에 차단하는 것이 ECR 운영의 핵심이다.

---

## 2. 설명

### 2.1 핵심 개념

**ECR 비용 구조**

| 항목 | 비용 |
|------|------|
| 스토리지 | $0.10/GB/월 (500MB 무료 — 프라이빗 기준) |
| 데이터 전송 (같은 리전 내) | 무료 |
| 데이터 전송 (다른 리전) | $0.09/GB |
| 이미지 Pull (인터넷) | $0.09/GB |

> 이미지 레이어가 공유되므로 실제 스토리지는 중복 제거 후 합산

**이미지 태그 전략**

```
권장 태그 조합:
  latest         → 항상 최신 (개발/스테이징에서만 사용)
  v1.2.3         → 시맨틱 버전 (Immutable tag 권장)
  git-abc1234    → Git 커밋 해시 (추적성)
  2024-01-15     → 날짜 기반

안티패턴:
  latest만 사용 → 어떤 버전이 배포됐는지 추적 불가
  태그 없음     → Lifecycle 정책으로 즉시 삭제 대상
```

**Lifecycle Policy 규칙 우선순위**

```
규칙 번호(rulePriority)가 낮을수록 먼저 평가
이미지가 한 규칙에 걸리면 다음 규칙은 평가 안 함

예시:
  Rule 1 (priority 1): latest 태그 → 유지
  Rule 2 (priority 2): 태그 없는 이미지 5개 초과 시 삭제
  Rule 3 (priority 3): 30일 이상 된 이미지 삭제
```

---

### 2.2 실무 적용 코드

**Terraform — ECR Repository + Lifecycle Policy**

```hcl
resource "aws_ecr_repository" "app" {
  name = "my-app"

  # 이미지 태그 변경 불가 (실수로 덮어쓰기 방지)
  image_tag_mutability = "IMMUTABLE"

  # 이미지 스캔 (Push 시 자동 취약점 스캔)
  image_scanning_configuration {
    scan_on_push = true
  }

  # 암호화
  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.ecr.arn
  }

  tags = { Name = "my-app" }
}

# Lifecycle Policy
resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name

  policy = jsonencode({
    rules = [
      # 규칙 1: latest 태그는 항상 유지 (최신 1개)
      {
        rulePriority = 1
        description  = "Keep latest tag"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["latest"]
          countType     = "imageCountMoreThan"
          countNumber   = 1
        }
        action = { type = "expire" }
      },
      # 규칙 2: release 태그 30개 유지
      {
        rulePriority = 2
        description  = "Keep last 30 release images"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["v", "release-"]
          countType     = "imageCountMoreThan"
          countNumber   = 30
        }
        action = { type = "expire" }
      },
      # 규칙 3: Git 커밋 해시 태그 이미지 14일 보관
      {
        rulePriority = 3
        description  = "Keep git-sha images for 14 days"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["git-", "sha-"]
          countType     = "sinceImagePushed"
          countUnit     = "days"
          countNumber   = 14
        }
        action = { type = "expire" }
      },
      # 규칙 4: 태그 없는 이미지 즉시 삭제 (push 후 1일)
      {
        rulePriority = 4
        description  = "Remove untagged images after 1 day"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 1
        }
        action = { type = "expire" }
      }
    ]
  })
}
```

**멀티 계정 Cross-Account 이미지 Pull**

```hcl
# ECR Repository Policy — 다른 계정에서 Pull 허용
resource "aws_ecr_repository_policy" "cross_account" {
  repository = aws_ecr_repository.app.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowCrossAccountPull"
        Effect = "Allow"
        Principal = {
          AWS = [
            "arn:aws:iam::PROD_ACCOUNT_ID:root",
            "arn:aws:iam::STAGING_ACCOUNT_ID:root"
          ]
        }
        Action = [
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:BatchCheckLayerAvailability"
        ]
      }
    ]
  })
}

# ECR Public Gallery (공개 이미지)
resource "aws_ecrpublic_repository" "public_app" {
  provider        = aws.us-east-1   # ECR Public은 us-east-1만
  repository_name = "my-public-app"

  catalog_data {
    description = "My public Docker image"
    architectures = ["x86-64", "ARM 64"]
    operating_systems = ["Linux"]
  }
}
```

**이미지 취약점 스캔 자동화 (Enhanced Scanning)**

```hcl
# Enhanced Scanning 활성화 (Inspector 기반, 실시간 재스캔)
resource "aws_ecr_registry_scanning_configuration" "main" {
  scan_type = "ENHANCED"   # BASIC(무료) 또는 ENHANCED(Inspector 비용)

  rule {
    scan_frequency = "CONTINUOUS_SCAN"   # 새 취약점 발견 시 자동 재스캔
    repository_filter {
      filter      = "*"
      filter_type = "WILDCARD"
    }
  }
}

# 취약점 발견 시 EventBridge 알람
resource "aws_cloudwatch_event_rule" "ecr_critical_vuln" {
  name = "ecr-critical-vulnerability"

  event_pattern = jsonencode({
    source      = ["aws.inspector2"]
    detail-type = ["Inspector2 Finding"]
    detail = {
      severity = ["CRITICAL", "HIGH"]
      type     = ["PACKAGE_VULNERABILITY"]
      resources = {
        type = ["AWS_ECR_CONTAINER_IMAGE"]
      }
    }
  })
}
```

**CI/CD 파이프라인 — 이미지 빌드 & Push**

```bash
#!/bin/bash
# GitHub Actions / CodeBuild에서 사용하는 이미지 빌드 스크립트

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION="ap-northeast-2"
REPO="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/my-app"
GIT_SHA=$(git rev-parse --short HEAD)
VERSION=$(cat VERSION)

# ECR 로그인
aws ecr get-login-password --region ${REGION} | \
  docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

# 멀티 아키텍처 빌드 (x86 + ARM64)
docker buildx create --use
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --tag "${REPO}:${VERSION}" \
  --tag "${REPO}:git-${GIT_SHA}" \
  --push \
  .

# 취약점 스캔 결과 확인 (빌드 실패 조건)
aws ecr wait image-scan-complete \
  --repository-name my-app \
  --image-id imageTag=${VERSION}

CRITICAL=$(aws ecr describe-image-scan-findings \
  --repository-name my-app \
  --image-id imageTag=${VERSION} \
  --query 'imageScanFindings.findingSeverityCounts.CRITICAL' \
  --output text)

if [ "${CRITICAL}" != "None" ] && [ "${CRITICAL}" -gt 0 ]; then
  echo "CRITICAL vulnerabilities found: ${CRITICAL}"
  exit 1
fi
```

---

### 2.3 보안/비용 Best Practice

- **IMMUTABLE 태그 설정**: 한번 Push된 태그는 덮어쓰기 불가. 배포 롤백 시 어떤 이미지가 있는지 신뢰 가능
- **Lifecycle Policy 필수 적용**: 없으면 이미지 무한 누적. 태그 없는 이미지는 1일, 일반 이미지는 30~50개 상한 권장
- **취약점 스캔 CI 게이트**: CRITICAL 취약점 있는 이미지는 배포 파이프라인 차단. ECR Enhanced Scanning + Inspector2 조합
- **Cross-Region 복제 비용**: ECR 리전 간 복제는 데이터 전송 비용 발생. 필요한 리전에만 Replication Rule 설정

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**이미지 Pull 실패 (EKS — ImagePullBackOff)**

```bash
# ECR 인증 토큰 만료 확인 (12시간 유효)
# EKS는 kubelet이 자동 갱신하지만 오래된 노드에서는 실패 가능

# 노드에서 직접 Pull 테스트
aws ecr get-login-password --region ap-northeast-2 | \
  docker login --username AWS --password-stdin \
  123456789012.dkr.ecr.ap-northeast-2.amazonaws.com

docker pull 123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/my-app:v1.0.0

# IRSA로 ECR Pull 권한 확인
aws iam simulate-principal-policy \
  --policy-source-arn arn:aws:iam::123456789012:role/eks-node-role \
  --action-names "ecr:GetDownloadUrlForLayer" "ecr:BatchGetImage" \
  --resource-arns "arn:aws:ecr:ap-northeast-2:123456789012:repository/my-app"
```

**Lifecycle Policy로 필요한 이미지가 삭제됨**

```bash
# Lifecycle Policy 시뮬레이션 (실제 삭제 전 미리보기)
aws ecr get-lifecycle-policy-preview \
  --repository-name my-app \
  --query 'previewResults[*].{Tag:imageDetails.imageTags,Action:action.type,AppliedRule:appliedRulePriority}'

# 결과 확인 후 정책 조정
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: 여러 ECR Repository에 동일 Lifecycle Policy를 적용하려면?**
A: Terraform의 `for_each`로 반복 적용하거나, AWS CLI 스크립트로 모든 Repository에 일괄 적용하세요. ECR Registry-level Lifecycle Policy는 없으므로 Repository별로 적용해야 합니다.

**Q: 이미지 레이어가 공유되는데 삭제해도 다른 이미지에 영향 없나요?**
A: ECR이 레이어 참조를 추적해 다른 이미지에서 사용 중인 레이어는 삭제하지 않습니다. 안전하게 삭제됩니다.

---

## 4. 모니터링 및 알람

```hcl
# ECR 스토리지 크기 모니터링
resource "aws_cloudwatch_metric_alarm" "ecr_storage" {
  alarm_name          = "ecr-storage-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "RepositorySize"
  namespace           = "AWS/ECR"
  period              = 86400
  statistic           = "Maximum"
  threshold           = 107374182400   # 100GB

  dimensions = {
    RepositoryName = aws_ecr_repository.app.name
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
}
```

**ECR 스토리지 현황 조회**

```bash
# 전체 Repository 크기 확인
aws ecr describe-repositories \
  --query 'repositories[*].repositoryName' \
  --output text | tr '\t' '\n' | while read repo; do
    SIZE=$(aws ecr describe-images --repository-name $repo \
      --query 'sum(imageDetails[*].imageSizeInBytes)' --output text)
    echo "$repo: $(echo "scale=2; $SIZE/1073741824" | bc) GB"
  done
```

---

## 5. TIP

- **ECR Pull Through Cache**: Docker Hub, Quay.io 등 외부 레지스트리 이미지를 ECR을 통해 캐싱. 외부 Pull 제한(Docker Hub 200회/6시간) 우회 가능
- **이미지 태그 Immutable + 시맨틱 버전**: Kubernetes Deployment에서 `imagePullPolicy: Always`와 `latest` 태그 조합은 금지. 항상 구체적인 버전 태그 사용
- **멀티 아키텍처 이미지 (Buildx)**: Graviton(ARM64) 노드를 EKS에 추가할 때 미리 멀티아치 이미지 준비. 단일 태그로 x86/ARM64 모두 지원
- **ECR 엔드포인트 비용 절감**: Private subnet에서 ECR Pull 시 Interface Endpoint 사용. NAT GW 대비 비용 절감 + 보안 강화 (`vpc-endpoint.md` 참고)
