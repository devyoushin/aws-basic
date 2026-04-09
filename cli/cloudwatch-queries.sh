#!/usr/bin/env bash
# CloudWatch 실무 쿼리 모음
# 사용법: ./cloudwatch-queries.sh <명령> [인수]

REGION="${AWS_DEFAULT_REGION:-ap-northeast-2}"
NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
ONE_HOUR_AGO=$(date -u -v-1H +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -u -d "1 hour ago" +"%Y-%m-%dT%H:%M:%SZ")
ONE_DAY_AGO=$(date -u -v-24H +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -u -d "24 hours ago" +"%Y-%m-%dT%H:%M:%SZ")

# ─── 알람 ─────────────────────────────────────────────────────────────────────

# ALARM 상태인 알람 목록
list_alarms_in_alarm() {
  aws cloudwatch describe-alarms \
    --region "$REGION" \
    --state-value ALARM \
    --query 'MetricAlarms[].[AlarmName, StateReason, MetricName, Namespace]' \
    --output table
}

# 전체 알람 상태 요약
list_all_alarm_states() {
  echo "[ALARM]"
  aws cloudwatch describe-alarms --region "$REGION" --state-value ALARM \
    --query 'MetricAlarms[].AlarmName' --output text | tr '\t' '\n' | sed 's/^/  /'

  echo ""
  echo "[INSUFFICIENT_DATA]"
  aws cloudwatch describe-alarms --region "$REGION" --state-value INSUFFICIENT_DATA \
    --query 'MetricAlarms[].AlarmName' --output text | tr '\t' '\n' | sed 's/^/  /'

  echo ""
  echo "[OK]"
  aws cloudwatch describe-alarms --region "$REGION" --state-value OK \
    --query 'length(MetricAlarms)' --output text | xargs -I{} echo "  {} 개"
}

# 특정 이름 패턴으로 알람 검색
search_alarms() {
  local pattern="${1:?검색할 알람 이름 패턴을 입력하세요}"

  aws cloudwatch describe-alarms \
    --region "$REGION" \
    --alarm-name-prefix "$pattern" \
    --query 'MetricAlarms[].[AlarmName, StateValue, MetricName]' \
    --output table
}

# ─── 메트릭 데이터 추출 ───────────────────────────────────────────────────────

# EC2 CPU 사용률 (최근 1시간, 5분 평균)
get_ec2_cpu() {
  local instance_id="${1:?인스턴스 ID를 입력하세요}"

  aws cloudwatch get-metric-statistics \
    --region "$REGION" \
    --namespace AWS/EC2 \
    --metric-name CPUUtilization \
    --dimensions Name=InstanceId,Value="$instance_id" \
    --start-time "$ONE_HOUR_AGO" \
    --end-time "$NOW" \
    --period 300 \
    --statistics Average Maximum \
    --query 'sort_by(Datapoints, &Timestamp)[].[Timestamp, Average, Maximum]' \
    --output table
}

# RDS CPU + DB 커넥션 수 (최근 1시간)
get_rds_metrics() {
  local db_identifier="${1:?DB 식별자를 입력하세요}"

  echo "[CPU 사용률]"
  aws cloudwatch get-metric-statistics \
    --region "$REGION" \
    --namespace AWS/RDS \
    --metric-name CPUUtilization \
    --dimensions Name=DBInstanceIdentifier,Value="$db_identifier" \
    --start-time "$ONE_HOUR_AGO" \
    --end-time "$NOW" \
    --period 300 \
    --statistics Average \
    --query 'sort_by(Datapoints, &Timestamp)[].[Timestamp, Average]' \
    --output table

  echo "[DB 커넥션 수]"
  aws cloudwatch get-metric-statistics \
    --region "$REGION" \
    --namespace AWS/RDS \
    --metric-name DatabaseConnections \
    --dimensions Name=DBInstanceIdentifier,Value="$db_identifier" \
    --start-time "$ONE_HOUR_AGO" \
    --end-time "$NOW" \
    --period 300 \
    --statistics Average Maximum \
    --query 'sort_by(Datapoints, &Timestamp)[].[Timestamp, Average, Maximum]' \
    --output table
}

# ALB 요청 수 + 5xx 에러율 (최근 1시간)
get_alb_error_rate() {
  local lb_arn_suffix="${1:?ALB ARN suffix를 입력하세요 (app/name/id 형식)}"

  echo "[5XX 에러 수]"
  aws cloudwatch get-metric-statistics \
    --region "$REGION" \
    --namespace AWS/ApplicationELB \
    --metric-name HTTPCode_ELB_5XX_Count \
    --dimensions Name=LoadBalancer,Value="$lb_arn_suffix" \
    --start-time "$ONE_HOUR_AGO" \
    --end-time "$NOW" \
    --period 300 \
    --statistics Sum \
    --query 'sort_by(Datapoints, &Timestamp)[].[Timestamp, Sum]' \
    --output table

  echo "[전체 요청 수]"
  aws cloudwatch get-metric-statistics \
    --region "$REGION" \
    --namespace AWS/ApplicationELB \
    --metric-name RequestCount \
    --dimensions Name=LoadBalancer,Value="$lb_arn_suffix" \
    --start-time "$ONE_HOUR_AGO" \
    --end-time "$NOW" \
    --period 300 \
    --statistics Sum \
    --query 'sort_by(Datapoints, &Timestamp)[].[Timestamp, Sum]' \
    --output table
}

# ─── Logs Insights 쿼리 ───────────────────────────────────────────────────────

# Logs Insights 쿼리 실행 (결과 대기 포함)
run_logs_insights() {
  local log_group="${1:?로그 그룹을 입력하세요}"
  local query="${2:?쿼리를 입력하세요}"
  local hours="${3:-1}"

  local start_time end_time query_id
  end_time=$(date +%s)
  start_time=$((end_time - hours * 3600))

  echo "쿼리 실행 중..."
  query_id=$(aws logs start-query \
    --region "$REGION" \
    --log-group-name "$log_group" \
    --start-time "$start_time" \
    --end-time "$end_time" \
    --query-string "$query" \
    --query 'queryId' \
    --output text)

  echo "Query ID: $query_id"

  # 완료될 때까지 폴링
  while true; do
    local status
    status=$(aws logs get-query-results \
      --region "$REGION" \
      --query-id "$query_id" \
      --query 'status' \
      --output text)

    if [[ "$status" == "Complete" ]]; then
      break
    elif [[ "$status" == "Failed" || "$status" == "Cancelled" ]]; then
      echo "쿼리 실패: $status"
      exit 1
    fi
    sleep 2
  done

  aws logs get-query-results \
    --region "$REGION" \
    --query-id "$query_id" \
    --query 'results[]'
}

# 에러 로그 빈도 분석 (상위 10개)
query_error_frequency() {
  local log_group="${1:?로그 그룹을 입력하세요}"

  run_logs_insights "$log_group" \
    "fields @timestamp, @message
     | filter @message like /ERROR|error|Exception/
     | stats count() as cnt by bin(5m)
     | sort cnt desc
     | limit 20" \
    1
}

# Lambda 콜드 스타트 분석
query_lambda_cold_starts() {
  local function_name="${1:?Lambda 함수 이름을 입력하세요}"

  run_logs_insights "/aws/lambda/$function_name" \
    "filter @type = 'REPORT'
     | fields @timestamp, @duration, @billedDuration, @initDuration, @memorySize, @maxMemoryUsed
     | filter ispresent(@initDuration)
     | stats count() as coldStarts, avg(@initDuration) as avgInitMs, max(@initDuration) as maxInitMs by bin(1h)" \
    24
}

# ─── 로그 그룹 관리 ───────────────────────────────────────────────────────────

# 보존 기간이 설정되지 않은 로그 그룹 (비용 낭비)
find_log_groups_no_retention() {
  echo "[보존 기간 미설정 로그 그룹 — 비용 무제한 증가 위험]"
  aws logs describe-log-groups \
    --region "$REGION" \
    --query 'logGroups[?!retentionInDays].[logGroupName, storedBytes]' \
    --output table
}

# 로그 그룹 크기 순 정렬
list_log_groups_by_size() {
  aws logs describe-log-groups \
    --region "$REGION" \
    --query 'sort_by(logGroups, &storedBytes) | reverse(@) | [].[logGroupName, storedBytes, retentionInDays]' \
    --output table
}

# ─── 실행 진입점 ──────────────────────────────────────────────────────────────
case "${1:-}" in
  alarms)          list_alarms_in_alarm ;;
  alarm-states)    list_all_alarm_states ;;
  search-alarm)    search_alarms "$2" ;;
  ec2-cpu)         get_ec2_cpu "$2" ;;
  rds)             get_rds_metrics "$2" ;;
  alb-error)       get_alb_error_rate "$2" ;;
  query)           run_logs_insights "$2" "$3" "$4" ;;
  error-freq)      query_error_frequency "$2" ;;
  cold-start)      query_lambda_cold_starts "$2" ;;
  no-retention)    find_log_groups_no_retention ;;
  log-size)        list_log_groups_by_size ;;
  *)
    echo "사용법: $0 <명령> [인수]"
    echo ""
    echo "  alarms              ALARM 상태인 알람 목록"
    echo "  alarm-states        전체 알람 상태 요약"
    echo "  search-alarm PREFIX 알람 이름 검색"
    echo "  ec2-cpu INSTANCE_ID EC2 CPU 사용률"
    echo "  rds DB_ID           RDS CPU + 커넥션 수"
    echo "  alb-error LB_SUFFIX ALB 5xx 에러율"
    echo "  query GROUP QUERY [HOURS]  Logs Insights 쿼리"
    echo "  error-freq LOG_GROUP       에러 빈도 분석"
    echo "  cold-start FUNCTION        Lambda 콜드스타트"
    echo "  no-retention               보존 기간 미설정 로그 그룹"
    echo "  log-size                   로그 그룹 크기 순 정렬"
    ;;
esac
