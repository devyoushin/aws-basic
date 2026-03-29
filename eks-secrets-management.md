# EKS Secrets 관리 — External Secrets Operator + AWS Secrets Manager

## 1. 개요

Kubernetes Secret은 base64 인코딩에 불과하며, etcd에 평문으로 저장된다.
민감한 정보(DB 비밀번호, API 키 등)는 AWS Secrets Manager 또는 SSM Parameter Store에 저장하고,
External Secrets Operator (ESO)를 통해 Kubernetes Secret으로 동기화하는 패턴이 보안 베스트 프랙티스다.

---

## 2. 설명

### 2.1 핵심 개념

**Kubernetes Secret의 한계**
- base64는 암호화가 아닌 인코딩 — 누구나 디코딩 가능
- etcd 기본 설정은 평문 저장 (별도 암호화 설정 필요)
- Git에 커밋 시 노출 위험
- 시크릿 갱신 시 Pod 재시작 필요 (ESO는 자동 갱신)

**AWS Secrets Manager vs SSM Parameter Store 비교**

| 항목 | Secrets Manager | SSM Parameter Store |
|------|----------------|---------------------|
| 자동 교체 | 지원 (RDS, Redshift 등 네이티브) | 미지원 |
| 비용 | $0.40/시크릿/월 + API 호출 | 무료 (Standard) / $0.05/고급 파라미터 |
| 크기 제한 | 65,536 bytes | 4KB (Standard) / 8KB (Advanced) |
| 교차 계정 | 지원 | 미지원 |
| 사용 추천 | DB 자격증명, API 키 | 설정값, 환경변수 |

**External Secrets Operator (ESO) 아키텍처**

```
Secrets Manager / SSM Parameter Store
    ↑ 조회 (IRSA 권한)
ExternalSecret (CRD)
    ↓ 동기화 (refreshInterval 주기)
Kubernetes Secret (자동 생성/갱신)
    ↑ 마운트
Pod (환경변수 또는 파일로 참조)
```

---

### 2.2 실무 적용 코드

**Helm으로 ESO 설치**

```bash
helm repo add external-secrets https://charts.external-secrets.io
helm install external-secrets external-secrets/external-secrets \
  --namespace external-secrets \
  --create-namespace \
  --set installCRDs=true
```

**IRSA — ESO에 Secrets Manager 접근 권한 부여**

```hcl
module "eso_irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.0"

  role_name = "external-secrets-operator"

  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["external-secrets:external-secrets"]
    }
  }
}

resource "aws_iam_role_policy" "eso" {
  name = "eso-secrets-access"
  role = module.eso_irsa.iam_role_name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
          "secretsmanager:DescribeSecret"
        ]
        Resource = "arn:aws:secretsmanager:ap-northeast-2:123456789012:secret:prod/*"
      },
      {
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
          "ssm:GetParameters",
          "ssm:GetParametersByPath"
        ]
        Resource = "arn:aws:ssm:ap-northeast-2:123456789012:parameter/prod/*"
      }
    ]
  })
}
```

**ClusterSecretStore — AWS 연결 설정**

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ClusterSecretStore
metadata:
  name: aws-secrets-manager
spec:
  provider:
    aws:
      service: SecretsManager
      region: ap-northeast-2
      auth:
        jwt:
          serviceAccountRef:
            name: external-secrets
            namespace: external-secrets
---
# SSM Parameter Store용
apiVersion: external-secrets.io/v1beta1
kind: ClusterSecretStore
metadata:
  name: aws-ssm
spec:
  provider:
    aws:
      service: ParameterStore
      region: ap-northeast-2
      auth:
        jwt:
          serviceAccountRef:
            name: external-secrets
            namespace: external-secrets
```

**ExternalSecret — Secrets Manager에서 동기화**

```yaml
# Secrets Manager의 JSON 시크릿에서 특정 키만 추출
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: db-credentials
  namespace: production
spec:
  refreshInterval: 1h          # 1시간마다 Secrets Manager와 동기화
  secretStoreRef:
    name: aws-secrets-manager
    kind: ClusterSecretStore
  target:
    name: db-secret            # 생성할 Kubernetes Secret 이름
    creationPolicy: Owner
  data:
    - secretKey: username      # Kubernetes Secret의 키 이름
      remoteRef:
        key: prod/myapp/db     # Secrets Manager 시크릿 이름
        property: username     # JSON 내 특정 필드
    - secretKey: password
      remoteRef:
        key: prod/myapp/db
        property: password
---
# Secrets Manager 시크릿 전체를 Kubernetes Secret으로 매핑
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: app-config
  namespace: production
spec:
  refreshInterval: 30m
  secretStoreRef:
    name: aws-secrets-manager
    kind: ClusterSecretStore
  target:
    name: app-config-secret
  dataFrom:
    - extract:
        key: prod/myapp/config   # JSON 전체를 개별 키로 분해
```

**Pod에서 ESO 생성 Secret 참조**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-app
  namespace: production
spec:
  template:
    spec:
      containers:
        - name: app
          image: my-app:latest
          env:
            - name: DB_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: db-secret      # ESO가 생성한 Secret
                  key: password
          envFrom:
            - secretRef:
                name: app-config-secret
```

---

### 2.3 보안/비용 Best Practice

- **Secrets Manager 자동 교체 활성화**: RDS 비밀번호는 Secrets Manager 네이티브 교체 사용
- **refreshInterval 설정**: 너무 짧으면 API 호출 비용 증가 ($0.05/10,000 API 호출)
- **네임스페이스 격리**: SecretStore (네임스페이스 스코프) vs ClusterSecretStore (클러스터 스코프) — 격리가 필요하면 SecretStore 사용
- **etcd 암호화**: EKS에서 envelope encryption 활성화로 etcd의 Secret 데이터 보호

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**SecretStore 연결 실패**

```bash
# ExternalSecret 상태 확인
kubectl describe externalsecret db-credentials -n production

# 흔한 오류: "could not get credentials"
# 원인: IRSA 설정 오류, ServiceAccount annotation 누락

# SecretStore 상태 확인
kubectl get clustersecretstores
kubectl describe clustersecretstore aws-secrets-manager
```

**GetSecretValue 권한 오류**

```bash
# IAM Role 권한 확인
aws iam get-role-policy \
  --role-name external-secrets-operator \
  --policy-name eso-secrets-access

# 시크릿 이름/ARN 확인 (Resource에 정확히 지정되어 있는지)
aws secretsmanager describe-secret --secret-id prod/myapp/db
```

**시크릿이 갱신되지 않음**

```bash
# ExternalSecret 마지막 동기화 시간 확인
kubectl get externalsecret db-credentials -n production \
  -o jsonpath='{.status.refreshTime}'

# 강제 갱신 (annotation 추가로 트리거)
kubectl annotate externalsecret db-credentials \
  -n production \
  force-sync=$(date +%s) --overwrite
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: Secrets Manager 시크릿을 갱신했는데 Pod에 반영이 안 됩니다**
A: ESO는 `refreshInterval`에 따라 Kubernetes Secret을 갱신하지만, 환경변수로 마운트된 Secret은 Pod 재시작 없이 자동 갱신되지 않습니다. 파일로 마운트하면 자동 갱신됩니다 (`volumeMounts` + `subPath` 없이). 또는 Reloader 같은 도구로 Secret 변경 시 Deployment 자동 재시작을 설정하세요.

**Q: 여러 AWS 계정의 시크릿을 하나의 클러스터에서 사용하고 싶어요**
A: ClusterSecretStore를 계정별로 별도 생성하고, 각각 다른 IAM Role (교차 계정 assume)을 지정하면 됩니다.

---

## 4. 모니터링 및 알람

```yaml
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: eso-alerts
  namespace: monitoring
spec:
  groups:
    - name: eso.rules
      rules:
        - alert: ExternalSecretSyncFailed
          expr: externalsecret_sync_calls_error > 0
          for: 5m
          labels:
            severity: critical
          annotations:
            summary: "ExternalSecret 동기화 실패 ({{ $labels.name }})"
            description: "Secrets Manager에서 시크릿을 가져오지 못했습니다"

        - alert: ExternalSecretNotRefreshed
          expr: |
            time() - externalsecret_status_condition_last_transition_time{
              condition="Ready", status="True"
            } > 7200    # 2시간 이상 동기화 안 됨
          for: 10m
          labels:
            severity: warning
```

---

## 5. TIP

- **Sealed Secrets 대안**: Git에 암호화된 Secret을 저장하고 싶다면 Sealed Secrets (Bitnami) 사용 가능 — ESO와 달리 AWS 불필요
- **Secret 버전 관리**: Secrets Manager는 AWSCURRENT/AWSPREVIOUS 버전 태그를 지원 — 교체 중 이전 버전으로 롤백 가능
- **Doppler, Vault 등**: ESO는 Secrets Manager 외에도 HashiCorp Vault, Doppler 등 다양한 Provider를 지원
