#!/usr/bin/env bash
# IAM 실무 쿼리 모음 — 권한 감사, 자격 증명 점검
# 사용법: ./iam-queries.sh <명령> [인수]

REGION="${AWS_DEFAULT_REGION:-ap-northeast-2}"

# ─── 현재 자격 증명 확인 ──────────────────────────────────────────────────────

# 현재 사용 중인 자격 증명 정보
whoami() {
  aws sts get-caller-identity --output table
}

# ─── IAM 사용자 ───────────────────────────────────────────────────────────────

# 모든 IAM 사용자 목록 (마지막 활동 포함)
list_users() {
  aws iam list-users \
    --query 'Users[].[UserName, UserId, CreateDate, PasswordLastUsed]' \
    --output table
}

# 액세스 키 마지막 사용 날짜 (오래된 키 탐지)
check_access_key_age() {
  echo "[모든 IAM 사용자 액세스 키 현황]"
  aws iam list-users --query 'Users[].UserName' --output text | tr '\t' '\n' | while read -r user; do
    aws iam list-access-keys --user-name "$user" \
      --query "AccessKeyMetadata[].[
        '${user}',
        AccessKeyId,
        Status,
        CreateDate
      ]" \
      --output text
  done | column -t
}

# 90일 이상 된 액세스 키 탐지
find_old_access_keys() {
  local cutoff
  cutoff=$(date -u -v-90d +"%Y-%m-%d" 2>/dev/null || date -u -d "90 days ago" +"%Y-%m-%d")

  echo "[90일 이상 된 액세스 키 (교체 권장)]"
  aws iam list-users --query 'Users[].UserName' --output text | tr '\t' '\n' | while read -r user; do
    aws iam list-access-keys --user-name "$user" \
      --query "AccessKeyMetadata[?CreateDate<='${cutoff}T00:00:00Z'].[
        '${user}', AccessKeyId, Status, CreateDate
      ]" \
      --output text
  done | column -t
}

# MFA 미설정 IAM 사용자 탐지 (보안 감사)
find_users_without_mfa() {
  echo "[MFA 미설정 IAM 사용자 — 보안 위험]"
  aws iam get-account-summary --query 'SummaryMap' --output table

  # 콘솔 로그인 가능하지만 MFA 없는 사용자
  aws iam list-users --query 'Users[].UserName' --output text | tr '\t' '\n' | while read -r user; do
    local mfa_count
    mfa_count=$(aws iam list-mfa-devices --user-name "$user" \
      --query 'length(MFADevices)' --output text)
    if [[ "$mfa_count" == "0" ]]; then
      # 콘솔 로그인 프로파일 있는지 확인
      if aws iam get-login-profile --user-name "$user" &>/dev/null; then
        echo "  $user (콘솔 접근 가능, MFA 없음)"
      fi
    fi
  done
}

# ─── IAM 역할 ─────────────────────────────────────────────────────────────────

# 모든 IAM 역할 목록
list_roles() {
  aws iam list-roles \
    --query 'Roles[].[RoleName, RoleId, CreateDate]' \
    --output table
}

# 특정 역할의 Trust Policy (신뢰 관계) 확인
get_role_trust_policy() {
  local role_name="${1:?역할 이름을 입력하세요}"

  aws iam get-role \
    --role-name "$role_name" \
    --query 'Role.AssumeRolePolicyDocument' \
    --output json | python3 -m json.tool
}

# 특정 역할에 연결된 정책 목록
list_role_policies() {
  local role_name="${1:?역할 이름을 입력하세요}"

  echo "[관리형 정책]"
  aws iam list-attached-role-policies \
    --role-name "$role_name" \
    --query 'AttachedPolicies[].[PolicyName, PolicyArn]' \
    --output table

  echo "[인라인 정책]"
  aws iam list-role-policies \
    --role-name "$role_name" \
    --query 'PolicyNames[]' \
    --output table
}

# EC2/EKS IRSA 역할 탐색 (OIDC Trust 포함 역할)
list_irsa_roles() {
  echo "[OIDC Trust가 포함된 역할 (IRSA 후보)]"
  aws iam list-roles \
    --query 'Roles[?contains(AssumeRolePolicyDocument.Statement[0].Principal.Federated, `oidc`)].[RoleName, RoleId]' \
    --output table
}

# ─── IAM 정책 ─────────────────────────────────────────────────────────────────

# 관리형 정책 목록 (내 계정 생성 정책만)
list_customer_managed_policies() {
  aws iam list-policies \
    --scope Local \
    --query 'Policies[].[PolicyName, PolicyId, AttachmentCount, CreateDate]' \
    --output table
}

# 특정 정책의 현재 버전 내용 확인
get_policy_document() {
  local policy_arn="${1:?정책 ARN을 입력하세요}"

  local version_id
  version_id=$(aws iam get-policy \
    --policy-arn "$policy_arn" \
    --query 'Policy.DefaultVersionId' \
    --output text)

  aws iam get-policy-version \
    --policy-arn "$policy_arn" \
    --version-id "$version_id" \
    --query 'PolicyVersion.Document' \
    --output json | python3 -m json.tool
}

# 어디에도 연결되지 않은 정책 (정리 대상)
find_unattached_policies() {
  echo "[미사용 관리형 정책 (정리 대상)]"
  aws iam list-policies \
    --scope Local \
    --query 'Policies[?AttachmentCount==`0`].[PolicyName, PolicyArn, CreateDate]' \
    --output table
}

# ─── 권한 시뮬레이션 ──────────────────────────────────────────────────────────

# 특정 역할이 특정 액션을 수행할 수 있는지 시뮬레이션
simulate_role_permission() {
  local role_arn="${1:?역할 ARN을 입력하세요}"
  local action="${2:?액션을 입력하세요 (예: s3:GetObject)}"
  local resource="${3:-*}"

  aws iam simulate-principal-policy \
    --policy-source-arn "$role_arn" \
    --action-names "$action" \
    --resource-arns "$resource" \
    --query 'EvaluationResults[].[EvalActionName, EvalDecision, EvalResourceName]' \
    --output table
}

# ─── 자격 증명 리포트 ────────────────────────────────────────────────────────

# 자격 증명 리포트 생성 및 다운로드 (전체 사용자 현황)
generate_credential_report() {
  echo "자격 증명 리포트 생성 중..."
  aws iam generate-credential-report

  sleep 3

  aws iam get-credential-report \
    --query 'Content' \
    --output text | base64 --decode
}

# ─── 실행 진입점 ──────────────────────────────────────────────────────────────
case "${1:-}" in
  whoami)             whoami ;;
  users)              list_users ;;
  key-age)            check_access_key_age ;;
  old-keys)           find_old_access_keys ;;
  no-mfa)             find_users_without_mfa ;;
  roles)              list_roles ;;
  trust)              get_role_trust_policy "$2" ;;
  role-policies)      list_role_policies "$2" ;;
  irsa-roles)         list_irsa_roles ;;
  policies)           list_customer_managed_policies ;;
  policy-doc)         get_policy_document "$2" ;;
  unattached)         find_unattached_policies ;;
  simulate)           simulate_role_permission "$2" "$3" "$4" ;;
  cred-report)        generate_credential_report ;;
  *)
    echo "사용법: $0 <명령> [인수]"
    echo ""
    echo "  whoami                현재 자격 증명"
    echo "  users                 IAM 사용자 목록"
    echo "  key-age               액세스 키 생성일"
    echo "  old-keys              90일 이상 된 키"
    echo "  no-mfa                MFA 미설정 사용자"
    echo "  roles                 역할 목록"
    echo "  trust ROLE            Trust Policy 확인"
    echo "  role-policies ROLE    역할 연결 정책"
    echo "  irsa-roles            IRSA 역할 목록"
    echo "  policies              커스텀 정책 목록"
    echo "  policy-doc ARN        정책 문서 확인"
    echo "  unattached            미사용 정책"
    echo "  simulate ARN ACTION [RESOURCE]  권한 시뮬레이션"
    echo "  cred-report           자격 증명 전체 리포트"
    ;;
esac
