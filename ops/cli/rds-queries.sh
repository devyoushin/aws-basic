#!/usr/bin/env bash
# RDS / Aurora 실무 쿼리 모음
# 사용법: ./rds-queries.sh <명령> [인수]

REGION="${AWS_DEFAULT_REGION:-ap-northeast-2}"

# ─── RDS 인스턴스 조회 ────────────────────────────────────────────────────────

# 모든 RDS 인스턴스 목록
list_rds_instances() {
  aws rds describe-db-instances \
    --region "$REGION" \
    --query 'DBInstances[].[
      DBInstanceIdentifier,
      DBInstanceClass,
      Engine,
      EngineVersion,
      DBInstanceStatus,
      MultiAZ,
      Endpoint.Address
    ]' \
    --output table
}

# 특정 RDS 인스턴스 상세
describe_rds_instance() {
  local db_id="${1:?DB 인스턴스 식별자를 입력하세요}"

  aws rds describe-db-instances \
    --region "$REGION" \
    --db-instance-identifier "$db_id" \
    --query 'DBInstances[0]' \
    --output json
}

# ─── Aurora 클러스터 ──────────────────────────────────────────────────────────

# Aurora 클러스터 목록
list_aurora_clusters() {
  aws rds describe-db-clusters \
    --region "$REGION" \
    --query 'DBClusters[].[
      DBClusterIdentifier,
      Engine,
      EngineVersion,
      Status,
      DatabaseName,
      Endpoint,
      ReaderEndpoint,
      MultiAZ
    ]' \
    --output table
}

# Aurora 클러스터 멤버 (Writer/Reader 구분)
list_cluster_members() {
  local cluster_id="${1:?클러스터 ID를 입력하세요}"

  aws rds describe-db-clusters \
    --region "$REGION" \
    --db-cluster-identifier "$cluster_id" \
    --query 'DBClusters[0].DBClusterMembers[].[
      DBInstanceIdentifier,
      IsClusterWriter,
      DBClusterParameterGroupStatus
    ]' \
    --output table
}

# ─── 파라미터 그룹 ────────────────────────────────────────────────────────────

# 파라미터 그룹 목록
list_parameter_groups() {
  aws rds describe-db-parameter-groups \
    --region "$REGION" \
    --query 'DBParameterGroups[].[
      DBParameterGroupName,
      DBParameterGroupFamily,
      Description
    ]' \
    --output table
}

# 파라미터 그룹의 non-default 파라미터만 조회
list_modified_parameters() {
  local pg_name="${1:?파라미터 그룹 이름을 입력하세요}"

  aws rds describe-db-parameters \
    --region "$REGION" \
    --db-parameter-group-name "$pg_name" \
    --source user \
    --query 'Parameters[].[ParameterName, ParameterValue, ApplyMethod]' \
    --output table
}

# ─── 스냅샷 ───────────────────────────────────────────────────────────────────

# 최근 자동 스냅샷 목록
list_automated_snapshots() {
  local db_id="${1:?DB 인스턴스 식별자를 입력하세요}"

  aws rds describe-db-snapshots \
    --region "$REGION" \
    --db-instance-identifier "$db_id" \
    --snapshot-type automated \
    --query 'DBSnapshots[].[
      DBSnapshotIdentifier,
      SnapshotCreateTime,
      AllocatedStorage,
      Status
    ]' \
    --output table
}

# 수동 스냅샷 목록 (전체 계정)
list_manual_snapshots() {
  aws rds describe-db-snapshots \
    --region "$REGION" \
    --snapshot-type manual \
    --query 'DBSnapshots[].[
      DBSnapshotIdentifier,
      DBInstanceIdentifier,
      SnapshotCreateTime,
      AllocatedStorage
    ]' \
    --output table
}

# ─── 유지보수 / 이벤트 ────────────────────────────────────────────────────────

# 대기 중인 유지보수 작업 확인
list_pending_maintenance() {
  aws rds describe-pending-maintenance-actions \
    --region "$REGION" \
    --query 'PendingMaintenanceActions[].[
      ResourceIdentifier,
      PendingMaintenanceActionDetails[0].Action,
      PendingMaintenanceActionDetails[0].AutoAppliedAfterDate,
      PendingMaintenanceActionDetails[0].ForcedApplyDate
    ]' \
    --output table
}

# RDS 이벤트 최근 24시간 조회
list_recent_events() {
  local hours="${1:-24}"
  local start_time
  start_time=$(date -u -v -"${hours}"H +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null \
    || date -u --date="${hours} hours ago" +"%Y-%m-%dT%H:%M:%SZ")

  aws rds describe-events \
    --region "$REGION" \
    --start-time "$start_time" \
    --query 'Events[].[SourceIdentifier, SourceType, Message, Date]' \
    --output table
}

# ─── 모니터링 ─────────────────────────────────────────────────────────────────

# CPU 사용률 최근 1시간 평균
get_cpu_utilization() {
  local db_id="${1:?DB 인스턴스 식별자를 입력하세요}"

  aws cloudwatch get-metric-statistics \
    --region "$REGION" \
    --namespace AWS/RDS \
    --metric-name CPUUtilization \
    --dimensions "Name=DBInstanceIdentifier,Value=${db_id}" \
    --start-time "$(date -u -v -1H +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -u --date='1 hour ago' +"%Y-%m-%dT%H:%M:%SZ")" \
    --end-time "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" \
    --period 300 \
    --statistics Average \
    --query 'sort_by(Datapoints, &Timestamp)[].[Timestamp, Average]' \
    --output table
}

# FreeableMemory 최근 1시간 (단위: Bytes → GB 변환 필요)
get_freeable_memory() {
  local db_id="${1:?DB 인스턴스 식별자를 입력하세요}"

  aws cloudwatch get-metric-statistics \
    --region "$REGION" \
    --namespace AWS/RDS \
    --metric-name FreeableMemory \
    --dimensions "Name=DBInstanceIdentifier,Value=${db_id}" \
    --start-time "$(date -u -v -1H +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -u --date='1 hour ago' +"%Y-%m-%dT%H:%M:%SZ")" \
    --end-time "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" \
    --period 300 \
    --statistics Average \
    --query 'sort_by(Datapoints, &Timestamp)[-1].[Timestamp, Average]' \
    --output table
}

# 연결 수 (DatabaseConnections)
get_connection_count() {
  local db_id="${1:?DB 인스턴스 식별자를 입력하세요}"

  aws cloudwatch get-metric-statistics \
    --region "$REGION" \
    --namespace AWS/RDS \
    --metric-name DatabaseConnections \
    --dimensions "Name=DBInstanceIdentifier,Value=${db_id}" \
    --start-time "$(date -u -v -1H +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -u --date='1 hour ago' +"%Y-%m-%dT%H:%M:%SZ")" \
    --end-time "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" \
    --period 60 \
    --statistics Maximum \
    --query 'sort_by(Datapoints, &Timestamp)[].[Timestamp, Maximum]' \
    --output table
}

# ─── 페일오버 ─────────────────────────────────────────────────────────────────

# Aurora 클러스터 수동 페일오버 (Writer → Reader 전환)
failover_aurora_cluster() {
  local cluster_id="${1:?클러스터 ID를 입력하세요}"
  local target_instance="${2:-}"  # 비어 있으면 Aurora가 자동 선택

  echo "[주의] Aurora 클러스터 페일오버를 실행합니다: $cluster_id"
  read -r -p "계속하시겠습니까? (yes/no): " confirm
  [[ "$confirm" != "yes" ]] && echo "취소되었습니다." && return

  if [[ -n "$target_instance" ]]; then
    aws rds failover-db-cluster \
      --region "$REGION" \
      --db-cluster-identifier "$cluster_id" \
      --target-db-instance-identifier "$target_instance"
  else
    aws rds failover-db-cluster \
      --region "$REGION" \
      --db-cluster-identifier "$cluster_id"
  fi

  echo "페일오버 요청 완료. 상태 확인:"
  aws rds describe-db-clusters \
    --region "$REGION" \
    --db-cluster-identifier "$cluster_id" \
    --query 'DBClusters[0].[DBClusterIdentifier, Status]' \
    --output text
}

# ─── 실행 진입점 ──────────────────────────────────────────────────────────────
case "${1:-}" in
  list)              list_rds_instances ;;
  describe)          describe_rds_instance "$2" ;;
  clusters)          list_aurora_clusters ;;
  members)           list_cluster_members "$2" ;;
  param-groups)      list_parameter_groups ;;
  params)            list_modified_parameters "$2" ;;
  auto-snapshots)    list_automated_snapshots "$2" ;;
  manual-snapshots)  list_manual_snapshots ;;
  maintenance)       list_pending_maintenance ;;
  events)            list_recent_events "${2:-24}" ;;
  cpu)               get_cpu_utilization "$2" ;;
  memory)            get_freeable_memory "$2" ;;
  connections)       get_connection_count "$2" ;;
  failover)          failover_aurora_cluster "$2" "${3:-}" ;;
  *)
    echo "사용법: $0 <명령> [인수]"
    echo ""
    echo "  list                       RDS 인스턴스 전체 목록"
    echo "  describe DB_ID             RDS 인스턴스 상세"
    echo "  clusters                   Aurora 클러스터 목록"
    echo "  members CLUSTER_ID         클러스터 멤버 (Writer/Reader)"
    echo "  param-groups               파라미터 그룹 목록"
    echo "  params PG_NAME             수정된 파라미터 조회"
    echo "  auto-snapshots DB_ID       자동 스냅샷 목록"
    echo "  manual-snapshots           수동 스냅샷 전체 목록"
    echo "  maintenance                대기 중인 유지보수 작업"
    echo "  events [HOURS]             최근 이벤트 (기본: 24시간)"
    echo "  cpu DB_ID                  CPU 사용률 (최근 1시간)"
    echo "  memory DB_ID               가용 메모리 (최근 1시간)"
    echo "  connections DB_ID          연결 수 (최근 1시간)"
    echo "  failover CLUSTER_ID [TARGET_INSTANCE]  Aurora 수동 페일오버"
    ;;
esac
