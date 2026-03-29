# EKS 클러스터 버전 업그레이드 전략

## 1. 개요

EKS는 Kubernetes 버전을 약 14개월 지원하며, EOL 이후에는 강제 업그레이드가 진행된다.
업그레이드 순서는 반드시 Control Plane → Managed Node Group → Add-on 순으로 진행해야 하며,
사전에 deprecated API 제거와 add-on 호환성을 검증해야 한다.

---

## 2. 설명

### 2.1 핵심 개념

**EKS 버전 지원 정책**
- 일반적으로 동시에 3~4개 마이너 버전 지원
- EOL 6개월 전 콘솔/이메일 경고 → EOL 시 자동 업그레이드 시작
- Standard Support: 출시 후 14개월
- Extended Support (유료): 이후 12~26개월 (버전당 약 $0.60/클러스터/시간 추가)

**업그레이드 순서 (중요)**

```
1. Control Plane 업그레이드 (한 마이너 버전씩)
   1.27 → 1.28 → 1.29 (건너뛰기 불가)

2. Managed Node Group 업그레이드
   (Control Plane과 최대 2 버전 차이까지 허용)

3. Add-on 업그레이드
   - kube-proxy
   - CoreDNS
   - VPC CNI (aws-node)
   - EBS CSI Driver 등
```

**인플레이스 vs 블루/그린 비교**

| 항목 | 인플레이스 업그레이드 | 블루/그린 클러스터 교체 |
|------|-------------------|----------------------|
| 다운타임 | 최소 (롤링) | 없음 (트래픽 전환) |
| 위험도 | 중간 | 낮음 |
| 비용 | 낮음 | 높음 (임시 이중 실행) |
| 복잡도 | 낮음 | 높음 |
| 권장 상황 | 소규모, 내부 서비스 | 대규모, 프로덕션 |

**Add-on 버전 호환성 매핑 (예시)**

| Kubernetes | VPC CNI | CoreDNS | kube-proxy |
|-----------|---------|---------|------------|
| 1.28 | v1.16.x | v1.10.x | v1.28.x |
| 1.29 | v1.18.x | v1.11.x | v1.29.x |
| 1.30 | v1.18.x | v1.11.x | v1.30.x |

→ EKS 콘솔 또는 AWS CLI로 각 버전의 지원 add-on 버전 확인 필수

---

### 2.2 실무 적용 코드

**업그레이드 전 사전 체크 — Deprecated API 확인**

```bash
# pluto: deprecated/removed API 사용 여부 스캔
brew install pluto

# 클러스터 내 리소스 스캔
pluto detect-in-cluster --target-versions k8s=v1.29

# 헬름 차트 스캔
pluto detect-helm --target-versions k8s=v1.29

# 출력 예시:
# NAME               KIND        VERSION              REPLACEMENT    REMOVED   DEPRECATED
# my-ingress         Ingress     networking.k8s.io/v1beta1   ...   true      true
```

```bash
# kubectl-convert: 구 API 매니페스트를 새 API로 변환
kubectl convert -f old-ingress.yaml --output-version networking.k8s.io/v1
```

**Control Plane 업그레이드**

```bash
# 현재 버전 확인
aws eks describe-cluster --name my-cluster \
  --query 'cluster.{version:version,status:status}'

# Control Plane 업그레이드 시작
aws eks update-cluster-version \
  --name my-cluster \
  --kubernetes-version 1.29

# 업그레이드 진행 상태 확인 (5~15분 소요)
aws eks describe-update \
  --name my-cluster \
  --update-id <update-id>

# 완료 대기
aws eks wait cluster-active --name my-cluster
```

**Managed Node Group 업그레이드**

```bash
# 노드그룹 현재 버전 확인
aws eks describe-nodegroup \
  --cluster-name my-cluster \
  --nodegroup-name my-nodegroup \
  --query 'nodegroup.{version:version,releaseVersion:releaseVersion}'

# 사용 가능한 AMI 버전 목록
aws eks describe-addon-versions \
  --kubernetes-version 1.29

# 노드그룹 업그레이드 (롤링 업데이트)
aws eks update-nodegroup-version \
  --cluster-name my-cluster \
  --nodegroup-name my-nodegroup \
  --kubernetes-version 1.29 \
  --update-config maxUnavailable=1   # 한 번에 최대 1개 노드 교체

# 업그레이드 상태 확인
aws eks describe-update \
  --name my-cluster \
  --nodegroup-name my-nodegroup \
  --update-id <update-id>
```

**Add-on 업그레이드**

```bash
# 현재 add-on 버전 확인
aws eks describe-addon \
  --cluster-name my-cluster \
  --addon-name coredns \
  --query 'addon.{version:addonVersion,status:status}'

# 지원되는 add-on 버전 목록
aws eks describe-addon-versions \
  --addon-name coredns \
  --kubernetes-version 1.29 \
  --query 'addons[*].addonVersions[*].addonVersion'

# add-on 업그레이드
aws eks update-addon \
  --cluster-name my-cluster \
  --addon-name coredns \
  --addon-version v1.11.1-eksbuild.4 \
  --resolve-conflicts OVERWRITE
```

**Terraform으로 업그레이드 관리**

```hcl
module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.0"

  cluster_name    = "my-cluster"
  cluster_version = "1.29"   # 버전 변경 후 terraform apply

  # 관리형 노드그룹은 cluster_version 변경 후 별도 업데이트 필요
  eks_managed_node_groups = {
    app = {
      min_size     = 2
      max_size     = 10
      desired_size = 3

      instance_types = ["m5.xlarge"]

      update_config = {
        max_unavailable = 1
      }
    }
  }
}
```

**업그레이드 전 PDB 확인**

```bash
# 모든 네임스페이스의 PDB 상태 확인
kubectl get pdb -A

# ALLOWED DISRUPTIONS가 0인 PDB 목록 (노드 교체 차단 가능)
kubectl get pdb -A -o json | jq \
  '.items[] | select(.status.disruptionsAllowed == 0) |
  {name: .metadata.name, namespace: .metadata.namespace}'
```

---

### 2.3 보안/비용 Best Practice

- **스테이징 클러스터 먼저 업그레이드**: 동일한 add-on, 워크로드로 검증 후 프로덕션 적용
- **한 번에 한 마이너 버전씩**: 1.27 → 1.29 건너뛰기 불가
- **업그레이드 직전 etcd 백업**: EKS는 자동 백업하지만 별도 스냅샷 권장
- **Extended Support 비용 관리**: 지원 기간 내 정기적 업그레이드로 추가 비용 방지

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**노드그룹 업그레이드 stuck — PDB 차단**

```bash
# 업그레이드 실패 이유 확인
aws eks describe-update \
  --name my-cluster \
  --nodegroup-name my-nodegroup \
  --update-id <update-id>
# "ErrorCode": "PodEvictionFailure"

# ALLOWED DISRUPTIONS 확인 및 수정
kubectl get pdb -A | grep "0 "
kubectl scale deployment my-app --replicas=4  # 임시로 replica 증가

# 또는 업그레이드 재시도
aws eks update-nodegroup-version \
  --cluster-name my-cluster \
  --nodegroup-name my-nodegroup \
  --force  # PDB 무시 (서비스 영향 있을 수 있음 — 비권장)
```

**Add-on 버전 충돌**

```bash
# add-on 업데이트 충돌 확인
aws eks describe-addon \
  --cluster-name my-cluster \
  --addon-name vpc-cni | grep -i conflict

# OVERWRITE로 충돌 해결
aws eks update-addon \
  --cluster-name my-cluster \
  --addon-name vpc-cni \
  --addon-version v1.18.0-eksbuild.1 \
  --resolve-conflicts OVERWRITE
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: 업그레이드 중 API 서버가 잠시 중단되나요?**
A: Control Plane 업그레이드 중 API 서버가 최대 수 초간 응답하지 않을 수 있습니다. 하지만 실행 중인 Pod는 영향받지 않습니다. 업그레이드는 오프 피크 시간대에 수행을 권장합니다.

**Q: 커스텀 add-on(Karpenter, ALB Controller 등)도 같이 업그레이드해야 하나요?**
A: 예. EKS 관리 add-on이 아닌 Helm으로 설치한 컴포넌트는 Kubernetes 버전 호환성을 별도로 확인하고 업그레이드해야 합니다.

---

## 4. 모니터링 및 알람

```hcl
# EKS 버전 EOL 30일 전 알람 (AWS Health 이벤트)
resource "aws_cloudwatch_event_rule" "eks_version_eol" {
  name = "eks-version-eol-warning"

  event_pattern = jsonencode({
    source      = ["aws.health"]
    detail-type = ["AWS Health Event"]
    detail = {
      service  = ["EKS"]
      eventTypeCode = ["AWS_EKS_PLANNED_LIFECYCLE_EVENT"]
    }
  })
}
```

---

## 5. TIP

- **업그레이드 체크리스트**:
  1. `pluto detect-in-cluster` — deprecated API 확인
  2. `kubectl get pdb -A` — ALLOWED DISRUPTIONS 확인
  3. 스테이징 클러스터 업그레이드 및 smoke test
  4. 업그레이드 공지 (슬랙/이메일)
  5. Control Plane 업그레이드 (5~15분)
  6. 노드그룹 롤링 업그레이드
  7. Add-on 업그레이드
  8. E2E 테스트 실행

- **자동 업그레이드 비활성화**: EKS는 EOL 이후 자동 업그레이드를 시작하므로 미리 계획 필요
- **EKS Upgrade Insights**: 콘솔에서 업그레이드 전 잠재적 문제 자동 탐지 기능 제공
