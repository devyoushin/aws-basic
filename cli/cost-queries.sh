#!/usr/bin/env bash
# AWS 비용 실무 쿼리 모음
# 사용법: ./cost-queries.sh <명령> [인수]

REGION="${AWS_DEFAULT_REGION:-ap-northeast-2}"
TODAY=$(date +"%Y-%m-%d")
FIRST_OF_MONTH=$(date +"%Y-%m-01")
LAST_MONTH_START=$(date -v-1m +"%Y-%m-01" 2>/dev/null || date -d "$(date +%Y-%m-01) -1 month" +"%Y-%m-%d")
LAST_MONTH_END=$(date +"%Y-%m-01")  # 이번 달 1일 = 지난달 말일 다음날

# ─── 이번 달 비용 ─────────────────────────────────────────────────────────────

# 이번 달 서비스별 비용 (내림차순)
cost_by_service_this_month() {
  aws ce get-cost-and-usage \
    --time-period Start="$FIRST_OF_MONTH",End="$TODAY" \
    --granularity MONTHLY \
    --metrics BlendedCost \
    --group-by Type=DIMENSION,Key=SERVICE \
    --query 'ResultsByTime[0].Groups | sort_by(@, &Keys[0]) | reverse(sort_by(@, &Metrics.BlendedCost.Amount)) | [].[Keys[0], Metrics.BlendedCost.Amount, Metrics.BlendedCost.Unit]' \
    --output table
}

# 이번 달 일별 총 비용 추이
daily_cost_this_month() {
  aws ce get-cost-and-usage \
    --time-period Start="$FIRST_OF_MONTH",End="$TODAY" \
    --granularity DAILY \
    --metrics BlendedCost \
    --query 'ResultsByTime[].[TimePeriod.Start, Total.BlendedCost.Amount]' \
    --output table
}

# ─── 지난달 비용 ──────────────────────────────────────────────────────────────

# 지난달 서비스별 비용
cost_by_service_last_month() {
  aws ce get-cost-and-usage \
    --time-period Start="$LAST_MONTH_START",End="$LAST_MONTH_END" \
    --granularity MONTHLY \
    --metrics BlendedCost \
    --group-by Type=DIMENSION,Key=SERVICE \
    --query 'ResultsByTime[0].Groups | reverse(sort_by(@, &Metrics.BlendedCost.Amount)) | [].[Keys[0], Metrics.BlendedCost.Amount]' \
    --output table
}

# ─── 태그별 비용 ──────────────────────────────────────────────────────────────

# 특정 태그별 비용 분류 (예: Environment=prod/dev/staging)
cost_by_tag() {
  local tag_key="${1:-Environment}"
  local start="${2:-$FIRST_OF_MONTH}"
  local end="${3:-$TODAY}"

  aws ce get-cost-and-usage \
    --time-period Start="$start",End="$end" \
    --granularity MONTHLY \
    --metrics BlendedCost \
    --group-by Type=TAG,Key="$tag_key" \
    --query 'ResultsByTime[0].Groups[].[Keys[0], Metrics.BlendedCost.Amount]' \
    --output table
}

# ─── 계정별 비용 (Organizations) ─────────────────────────────────────────────

cost_by_account() {
  local start="${1:-$FIRST_OF_MONTH}"
  local end="${2:-$TODAY}"

  aws ce get-cost-and-usage \
    --time-period Start="$start",End="$end" \
    --granularity MONTHLY \
    --metrics BlendedCost \
    --group-by Type=DIMENSION,Key=LINKED_ACCOUNT \
    --query 'ResultsByTime[0].Groups | reverse(sort_by(@, &Metrics.BlendedCost.Amount)) | [].[Keys[0], Metrics.BlendedCost.Amount]' \
    --output table
}

# ─── 비용 이상 탐지 ───────────────────────────────────────────────────────────

# 어제 vs 같은 기간 지난주 비용 비교
detect_cost_spike() {
  local yesterday
  local last_week_same_day
  yesterday=$(date -v-1d +"%Y-%m-%d" 2>/dev/null || date -d "yesterday" +"%Y-%m-%d")
  last_week_same_day=$(date -v-8d +"%Y-%m-%d" 2>/dev/null || date -d "8 days ago" +"%Y-%m-%d")
  local last_week_yesterday
  last_week_yesterday=$(date -v-7d +"%Y-%m-%d" 2>/dev/null || date -d "7 days ago" +"%Y-%m-%d")

  echo "[어제 비용]"
  aws ce get-cost-and-usage \
    --time-period Start="$yesterday",End="$TODAY" \
    --granularity DAILY \
    --metrics BlendedCost \
    --query 'ResultsByTime[].[TimePeriod.Start, Total.BlendedCost.Amount]' \
    --output table

  echo "[지난주 같은 요일 비용]"
  aws ce get-cost-and-usage \
    --time-period Start="$last_week_same_day",End="$last_week_yesterday" \
    --granularity DAILY \
    --metrics BlendedCost \
    --query 'ResultsByTime[].[TimePeriod.Start, Total.BlendedCost.Amount]' \
    --output table
}

# ─── EC2 리소스 비용 최적화 탐지 ─────────────────────────────────────────────

# 미연결 EIP 목록 (시간당 $0.005 과금)
find_unused_eip() {
  echo "[미연결 EIP — 즉시 삭제 권장]"
  aws ec2 describe-addresses \
    --region "$REGION" \
    --query 'Addresses[?AssociationId==null].[PublicIp, AllocationId]' \
    --output table
}

# 미연결 EBS 볼륨 (프로비전된 스토리지 과금)
find_unused_ebs() {
  echo "[미연결 EBS 볼륨 — 스냅샷 후 삭제 권장]"
  aws ec2 describe-volumes \
    --region "$REGION" \
    --filters "Name=status,Values=available" \
    --query 'Volumes[].[VolumeId, Size, VolumeType, CreateTime]' \
    --output table
}

# 중지된 EC2 인스턴스 (EBS, EIP 비용 계속 발생)
find_stopped_instances() {
  echo "[중지된 EC2 인스턴스 — EBS/EIP 비용 발생 중]"
  aws ec2 describe-instances \
    --region "$REGION" \
    --filters "Name=instance-state-name,Values=stopped" \
    --query 'Reservations[].Instances[].[InstanceId, Tags[?Key==`Name`].Value | [0], InstanceType, StateTransitionReason]' \
    --output table
}

# 오래된 스냅샷 목록 (30일 이상)
find_old_snapshots() {
  local cutoff
  cutoff=$(date -u -v-30d +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -u -d "30 days ago" +"%Y-%m-%dT%H:%M:%SZ")

  local owner_id
  owner_id=$(aws sts get-caller-identity --query Account --output text)

  echo "[30일 이상 된 스냅샷]"
  aws ec2 describe-snapshots \
    --region "$REGION" \
    --owner-ids "$owner_id" \
    --query "Snapshots[?StartTime<='$cutoff'].[SnapshotId, VolumeSize, StartTime, Description]" \
    --output table
}

# ─── 리전별 비용 ──────────────────────────────────────────────────────────────

# 리전별 이번 달 비용
cost_by_region() {
  aws ce get-cost-and-usage \
    --time-period Start="$FIRST_OF_MONTH",End="$TODAY" \
    --granularity MONTHLY \
    --metrics BlendedCost \
    --group-by Type=DIMENSION,Key=REGION \
    --query 'ResultsByTime[0].Groups | reverse(sort_by(@, &Metrics.BlendedCost.Amount)) | [].[Keys[0], Metrics.BlendedCost.Amount]' \
    --output table
}

# ─── 전월 대비 비교 ───────────────────────────────────────────────────────────

# 이번 달 vs 지난달 서비스별 비용 비교
compare_month_over_month() {
  echo "[지난달 서비스별 비용]"
  aws ce get-cost-and-usage \
    --time-period Start="$LAST_MONTH_START",End="$LAST_MONTH_END" \
    --granularity MONTHLY \
    --metrics BlendedCost \
    --group-by Type=DIMENSION,Key=SERVICE \
    --query 'ResultsByTime[0].Groups | reverse(sort_by(@, &Metrics.BlendedCost.Amount)) | [0:10].[Keys[0], Metrics.BlendedCost.Amount]' \
    --output table

  echo ""
  echo "[이번 달 서비스별 비용 (현재까지)]"
  aws ce get-cost-and-usage \
    --time-period Start="$FIRST_OF_MONTH",End="$TODAY" \
    --granularity MONTHLY \
    --metrics BlendedCost \
    --group-by Type=DIMENSION,Key=SERVICE \
    --query 'ResultsByTime[0].Groups | reverse(sort_by(@, &Metrics.BlendedCost.Amount)) | [0:10].[Keys[0], Metrics.BlendedCost.Amount]' \
    --output table
}

# ─── 비용 예측 ────────────────────────────────────────────────────────────────

# 이번 달 말까지 비용 예측
forecast_monthly_cost() {
  local end_of_month
  end_of_month=$(date -v+1m -v1d +"%Y-%m-%d" 2>/dev/null \
    || date -d "$(date +%Y-%m-01) +1 month" +"%Y-%m-%d")

  aws ce get-cost-forecast \
    --time-period Start="$TODAY",End="$end_of_month" \
    --metric BLENDED_COST \
    --granularity MONTHLY \
    --query '[Total.[Amount, Unit], ForecastResultsByTime[0].[PredictionIntervalLowerBound, PredictionIntervalUpperBound]]' \
    --output json
}

# ─── Savings Plans / RI 현황 ──────────────────────────────────────────────────

# Savings Plans 현황 (목록)
list_savings_plans() {
  aws savingsplans describe-savings-plans \
    --query 'savingsPlans[].[savingsPlanId, savingsPlanType, state, commitment, currency, termDurationInSeconds]' \
    --output table
}

# Savings Plans 활용률 (최근 30일)
savings_plans_utilization() {
  local start
  start=$(date -v-30d +"%Y-%m-%d" 2>/dev/null || date -d "30 days ago" +"%Y-%m-%d")

  aws ce get-savings-plans-utilization \
    --time-period Start="$start",End="$TODAY" \
    --query 'Total.[TotalCommitment, UsedCommitment, UnusedCommitment, UtilizationPercentage]' \
    --output table
}

# RI 활용률 서비스별 (최근 30일)
ri_utilization() {
  local start
  start=$(date -v-30d +"%Y-%m-%d" 2>/dev/null || date -d "30 days ago" +"%Y-%m-%d")

  aws ce get-reservation-utilization \
    --time-period Start="$start",End="$TODAY" \
    --group-by Type=DIMENSION,Key=SERVICE \
    --query 'UtilizationsByTime[0].Groups[].[Keys[0], Utilization.UtilizationPercentage, Utilization.PurchasedHours, Utilization.UsedHours, Utilization.UnusedHours]' \
    --output table
}

# ─── 실행 진입점 ──────────────────────────────────────────────────────────────
case "${1:-}" in
  this-month)     cost_by_service_this_month ;;
  daily)          daily_cost_this_month ;;
  last-month)     cost_by_service_last_month ;;
  by-tag)         cost_by_tag "$2" "$3" "$4" ;;
  by-account)     cost_by_account "$2" "$3" ;;
  by-region)      cost_by_region ;;
  mom)            compare_month_over_month ;;
  forecast)       forecast_monthly_cost ;;
  spike)          detect_cost_spike ;;
  unused-eip)     find_unused_eip ;;
  unused-ebs)     find_unused_ebs ;;
  stopped)        find_stopped_instances ;;
  old-snapshots)  find_old_snapshots ;;
  savings-plans)  list_savings_plans ;;
  sp-util)        savings_plans_utilization ;;
  ri-util)        ri_utilization ;;
  *)
    echo "사용법: $0 <명령> [인수]"
    echo ""
    echo "  this-month              이번 달 서비스별 비용"
    echo "  daily                   이번 달 일별 비용"
    echo "  last-month              지난달 서비스별 비용"
    echo "  by-tag [TAG] [START] [END]  태그별 비용"
    echo "  by-account [START] [END]    계정별 비용"
    echo "  by-region               리전별 비용"
    echo "  mom                     이번 달 vs 지난달 비교"
    echo "  forecast                월말 비용 예측"
    echo "  spike                   비용 급등 탐지 (어제 vs 지난주)"
    echo "  unused-eip              미연결 EIP"
    echo "  unused-ebs              미연결 EBS"
    echo "  stopped                 중지된 EC2"
    echo "  old-snapshots           오래된 스냅샷 (30일+)"
    echo "  savings-plans           Savings Plans 목록"
    echo "  sp-util                 Savings Plans 활용률"
    echo "  ri-util                 RI 활용률 서비스별"
    ;;
esac
