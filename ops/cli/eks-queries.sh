#!/usr/bin/env bash
# EKS 실무 쿼리 모음
# 사용법: ./eks-queries.sh <명령> [인수]

REGION="${AWS_DEFAULT_REGION:-ap-northeast-2}"

# ─── 클러스터 기본 정보 ───────────────────────────────────────────────────────

# 전체 EKS 클러스터 목록
list_clusters() {
  aws eks list-clusters \
    --region "$REGION" \
    --query 'clusters[]' \
    --output table
}

# 클러스터 상세 정보 (버전, 엔드포인트, OIDC)
describe_cluster() {
  local cluster="${1:?클러스터 이름을 입력하세요}"

  aws eks describe-cluster \
    --region "$REGION" \
    --name "$cluster" \
    --query 'cluster.{
      Name: name,
      Version: version,
      Status: status,
      Endpoint: endpoint,
      OIDC: identity.oidc.issuer,
      CreatedAt: createdAt
    }' \
    --output table
}

# ─── 노드 그룹 ────────────────────────────────────────────────────────────────

# 클러스터 내 모든 노드 그룹 목록
list_nodegroups() {
  local cluster="${1:?클러스터 이름을 입력하세요}"

  aws eks list-nodegroups \
    --region "$REGION" \
    --cluster-name "$cluster" \
    --query 'nodegroups[]' \
    --output table
}

# 노드 그룹 상세 (인스턴스 타입, 스케일링 설정, AMI)
describe_nodegroup() {
  local cluster="${1:?클러스터 이름을 입력하세요}"
  local nodegroup="${2:?노드 그룹 이름을 입력하세요}"

  aws eks describe-nodegroup \
    --region "$REGION" \
    --cluster-name "$cluster" \
    --nodegroup-name "$nodegroup" \
    --query 'nodegroup.{
      Name: nodegroupName,
      Status: status,
      InstanceTypes: instanceTypes,
      DesiredSize: scalingConfig.desiredSize,
      MinSize: scalingConfig.minSize,
      MaxSize: scalingConfig.maxSize,
      AMIType: amiType,
      ReleaseVersion: releaseVersion
    }' \
    --output table
}

# 모든 노드 그룹 용량 한눈에 보기
list_all_nodegroup_capacity() {
  local cluster="${1:?클러스터 이름을 입력하세요}"

  local nodegroups
  nodegroups=$(aws eks list-nodegroups \
    --region "$REGION" \
    --cluster-name "$cluster" \
    --query 'nodegroups[]' \
    --output text)

  echo "클러스터: $cluster"
  printf "%-40s %-8s %-8s %-8s %-10s\n" "NodeGroup" "Min" "Max" "Desired" "Status"
  echo "──────────────────────────────────────────────────────────────────────────"

  for ng in $nodegroups; do
    aws eks describe-nodegroup \
      --region "$REGION" \
      --cluster-name "$cluster" \
      --nodegroup-name "$ng" \
      --query 'nodegroup.[nodegroupName, scalingConfig.minSize, scalingConfig.maxSize, scalingConfig.desiredSize, status]' \
      --output text | awk '{printf "%-40s %-8s %-8s %-8s %-10s\n", $1, $2, $3, $4, $5}'
  done
}

# ─── 애드온 ───────────────────────────────────────────────────────────────────

# 클러스터 애드온 목록 및 버전
list_addons() {
  local cluster="${1:?클러스터 이름을 입력하세요}"

  aws eks list-addons \
    --region "$REGION" \
    --cluster-name "$cluster" \
    --query 'addons[]' \
    --output text | while read -r addon; do
      aws eks describe-addon \
        --region "$REGION" \
        --cluster-name "$cluster" \
        --addon-name "$addon" \
        --query 'addon.[addonName, addonVersion, status, serviceAccountRoleArn]' \
        --output text
    done | column -t
}

# 특정 애드온의 사용 가능한 최신 버전 확인
check_addon_latest_version() {
  local cluster="${1:?클러스터 이름을 입력하세요}"
  local addon="${2:-vpc-cni}"

  local k8s_version
  k8s_version=$(aws eks describe-cluster \
    --region "$REGION" \
    --name "$cluster" \
    --query 'cluster.version' \
    --output text)

  aws eks describe-addon-versions \
    --region "$REGION" \
    --kubernetes-version "$k8s_version" \
    --addon-name "$addon" \
    --query 'addons[0].addonVersions[].[addonVersion, compatibilities[0].defaultVersion]' \
    --output table
}

# ─── IRSA / OIDC ──────────────────────────────────────────────────────────────

# 클러스터 OIDC Provider URL 출력 (IRSA 설정 시 필요)
get_oidc_issuer() {
  local cluster="${1:?클러스터 이름을 입력하세요}"

  aws eks describe-cluster \
    --region "$REGION" \
    --name "$cluster" \
    --query 'cluster.identity.oidc.issuer' \
    --output text
}

# 계정에 등록된 OIDC Provider 목록
list_oidc_providers() {
  aws iam list-open-id-connect-providers \
    --query 'OpenIDConnectProviderList[].Arn' \
    --output table
}

# ─── 업그레이드 관련 ──────────────────────────────────────────────────────────

# 클러스터 버전 대비 지원 가능한 버전 목록
list_supported_versions() {
  aws eks describe-addon-versions \
    --region "$REGION" \
    --query 'addons[0].addonVersions[].compatibilities[].kubernetesVersion' \
    --output text | tr '\t' '\n' | sort -u
}

# 노드 그룹 AMI 릴리즈 버전 확인 (업그레이드 전 현황 파악)
check_nodegroup_ami_version() {
  local cluster="${1:?클러스터 이름을 입력하세요}"
  local nodegroup="${2:?노드 그룹 이름을 입력하세요}"

  aws eks describe-nodegroup \
    --region "$REGION" \
    --cluster-name "$cluster" \
    --nodegroup-name "$nodegroup" \
    --query 'nodegroup.{AMIType: amiType, ReleaseVersion: releaseVersion, K8sVersion: version}' \
    --output table
}

# ─── kubectl 연동 ─────────────────────────────────────────────────────────────

# kubeconfig 업데이트
update_kubeconfig() {
  local cluster="${1:?클러스터 이름을 입력하세요}"
  aws eks update-kubeconfig \
    --region "$REGION" \
    --name "$cluster"
}

# ─── 실행 진입점 ──────────────────────────────────────────────────────────────
case "${1:-}" in
  list)             list_clusters ;;
  desc)             describe_cluster "$2" ;;
  ng-list)          list_nodegroups "$2" ;;
  ng-desc)          describe_nodegroup "$2" "$3" ;;
  ng-capacity)      list_all_nodegroup_capacity "$2" ;;
  addons)           list_addons "$2" ;;
  addon-version)    check_addon_latest_version "$2" "$3" ;;
  oidc)             get_oidc_issuer "$2" ;;
  oidc-list)        list_oidc_providers ;;
  ami-version)      check_nodegroup_ami_version "$2" "$3" ;;
  kubeconfig)       update_kubeconfig "$2" ;;
  *)
    echo "사용법: $0 <명령> [인수]"
    echo ""
    echo "  list                    클러스터 목록"
    echo "  desc CLUSTER            클러스터 상세"
    echo "  ng-list CLUSTER         노드 그룹 목록"
    echo "  ng-desc CLUSTER NG      노드 그룹 상세"
    echo "  ng-capacity CLUSTER     모든 노드 그룹 용량"
    echo "  addons CLUSTER          애드온 목록 및 버전"
    echo "  addon-version CLUSTER ADDON  최신 애드온 버전"
    echo "  oidc CLUSTER            OIDC Issuer URL"
    echo "  oidc-list               OIDC Provider 목록"
    echo "  ami-version CLUSTER NG  AMI 릴리즈 버전"
    echo "  kubeconfig CLUSTER      kubeconfig 업데이트"
    ;;
esac
