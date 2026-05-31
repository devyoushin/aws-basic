#!/usr/bin/env bash
# 마이그레이션 실무 쿼리 모음 (MGN, DMS, DataSync, Migration Hub, ADS)
# 사용법: 필요한 함수만 복붙하거나, 전체 실행 시 ./migration-queries.sh <명령>

REGION="${AWS_DEFAULT_REGION:-ap-northeast-2}"

# ─── Application Discovery Service (ADS) ─────────────────────────────────────

# 탐지된 서버 목록 조회
list_discovered_servers() {
  aws discovery describe-agents \
    --region "$REGION" \
    --query 'agents[].[agentId, hostName, agentType, agentNetworkInfoList[0].ipAddress, connectorId]' \
    --output table
}

# 서버 간 네트워크 연결 관계 내보내기 시작
start_export_network_connections() {
  aws discovery start-export-task \
    --region "$REGION" \
    --filters name=resourceType,condition=EQUALS,values=SERVER \
    --query 'exportId' \
    --output text
}

# ADS 내보내기 작업 상태 확인
get_export_status() {
  local export_id="${1:?export_id 필요}"
  aws discovery describe-export-tasks \
    --region "$REGION" \
    --export-ids "$export_id" \
    --query 'exportsInfo[].[exportId, exportStatus, statusMessage, exportRequestTime]' \
    --output table
}

# ─── Migration Hub ─────────────────────────────────────────────────────────────

# Migration Hub 홈 리전 설정 (최초 1회)
set_migration_hub_home_region() {
  aws migrationhub-config create-home-region-control \
    --home-region "$REGION" \
    --target Type=ACCOUNT \
    --query 'HomeRegionControl.HomeRegion' \
    --output text
}

# Migration Hub 홈 리전 확인
get_migration_hub_home_region() {
  aws migrationhub-config describe-home-region-controls \
    --query 'HomeRegionControls[].[HomeRegion, Target.Type, RequestedTime]' \
    --output table
}

# Migration Hub 마이그레이션 작업 목록
list_migration_tasks() {
  aws migrationhub list-migration-tasks \
    --query 'MigrationTaskSummaryList[].[MigrationTaskName, Status, ProgressPercent, StatusDetail]' \
    --output table
}

# ─── Application Migration Service (MGN) — Rehost ──────────────────────────

# MGN 소스 서버 목록 (복제 상태 포함)
list_mgn_source_servers() {
  aws mgn describe-source-servers \
    --region "$REGION" \
    --query 'items[].[
      sourceServerID,
      dataReplicationInfo.dataReplicationState,
      dataReplicationInfo.lagDuration,
      dataReplicationInfo.etaDateTime,
      lifeCycle.state
    ]' \
    --output table
}

# MGN 소스 서버 상세 (특정 서버)
get_mgn_source_server() {
  local server_id="${1:?sourceServerID 필요}"
  aws mgn describe-source-servers \
    --region "$REGION" \
    --filters sourceServerIDs="$server_id" \
    --output json | jq '.items[0] | {
      id: .sourceServerID,
      hostname: .sourceProperties.identificationHints.hostname,
      replication_state: .dataReplicationInfo.dataReplicationState,
      lag: .dataReplicationInfo.lagDuration,
      lifecycle: .lifeCycle.state,
      cpu: .sourceProperties.cpus[0].modelName,
      ram_mb: .sourceProperties.ramBytes
    }'
}

# MGN Launch Configuration 조회 (인스턴스 타입, 서브넷 등)
get_mgn_launch_config() {
  local server_id="${1:?sourceServerID 필요}"
  aws mgn get-launch-configuration \
    --region "$REGION" \
    --source-server-id "$server_id" \
    --output json | jq '{
      name: .name,
      instance_type: .ec2LaunchTemplateData.instanceType,
      launch_disposition: .launchDisposition,
      target_instance_type_right_sizing_method: .targetInstanceTypeRightSizingMethod
    }'
}

# MGN 테스트 실행 시작
start_mgn_test() {
  local server_id="${1:?sourceServerID 필요}"
  aws mgn start-test \
    --region "$REGION" \
    --source-server-i-ds "$server_id" \
    --query 'job.jobID' \
    --output text
}

# MGN Cutover 실행 (실제 전환)
start_mgn_cutover() {
  local server_id="${1:?sourceServerID 필요}"
  echo "⚠️  Cutover를 실행합니다: $server_id"
  echo "계속하려면 'yes' 입력:"
  read -r confirm
  if [[ "$confirm" != "yes" ]]; then
    echo "취소됨"
    return 1
  fi
  aws mgn start-cutover \
    --region "$REGION" \
    --source-server-i-ds "$server_id" \
    --query 'job.jobID' \
    --output text
}

# MGN Job 상태 확인
get_mgn_job_status() {
  local job_id="${1:?jobID 필요}"
  aws mgn describe-jobs \
    --region "$REGION" \
    --filters jobIDs="$job_id" \
    --query 'items[].[jobID, status, type, initiatedBy, endDateTime]' \
    --output table
}

# MGN 복제 상태 요약 (전체 서버)
summary_mgn_replication() {
  aws mgn describe-source-servers \
    --region "$REGION" \
    --query 'items[].[
      sourceServerID,
      dataReplicationInfo.dataReplicationState,
      dataReplicationInfo.lagDuration,
      lifeCycle.state
    ]' \
    --output table
}

# ─── Database Migration Service (DMS) ─────────────────────────────────────────

# DMS Replication Instance 목록
list_dms_replication_instances() {
  aws dms describe-replication-instances \
    --region "$REGION" \
    --query 'ReplicationInstances[].[
      ReplicationInstanceIdentifier,
      ReplicationInstanceClass,
      ReplicationInstanceStatus,
      PubliclyAccessible,
      MultiAZ,
      AllocatedStorage
    ]' \
    --output table
}

# DMS Endpoint 목록 (소스/대상)
list_dms_endpoints() {
  aws dms describe-endpoints \
    --region "$REGION" \
    --query 'Endpoints[].[EndpointIdentifier, EndpointType, EngineName, Status, ServerName, DatabaseName]' \
    --output table
}

# DMS Endpoint 연결 테스트
test_dms_endpoint() {
  local replication_instance_arn="${1:?replication_instance_arn 필요}"
  local endpoint_arn="${2:?endpoint_arn 필요}"
  aws dms test-connection \
    --region "$REGION" \
    --replication-instance-arn "$replication_instance_arn" \
    --endpoint-arn "$endpoint_arn" \
    --query 'Connection.Status' \
    --output text
}

# DMS 마이그레이션 Task 목록 및 상태
list_dms_tasks() {
  aws dms describe-replication-tasks \
    --region "$REGION" \
    --query 'ReplicationTasks[].[
      ReplicationTaskIdentifier,
      Status,
      MigrationType,
      ReplicationTaskStats.FullLoadProgressPercent,
      ReplicationTaskStats.CDCLatencySource,
      ReplicationTaskStats.CDCLatencyTarget
    ]' \
    --output table
}

# DMS Task 시작
start_dms_task() {
  local task_arn="${1:?task_arn 필요}"
  local start_type="${2:-start-replication}"  # start-replication | resume-processing | reload-target
  aws dms start-replication-task \
    --region "$REGION" \
    --replication-task-arn "$task_arn" \
    --start-replication-task-type "$start_type" \
    --query 'ReplicationTask.Status' \
    --output text
}

# DMS Task 중지
stop_dms_task() {
  local task_arn="${1:?task_arn 필요}"
  aws dms stop-replication-task \
    --region "$REGION" \
    --replication-task-arn "$task_arn" \
    --query 'ReplicationTask.Status' \
    --output text
}

# DMS CDC Lag 확인 (Cutover 판단 기준: Lag < 60초)
check_dms_cdc_lag() {
  aws dms describe-replication-tasks \
    --region "$REGION" \
    --query 'ReplicationTasks[].[
      ReplicationTaskIdentifier,
      Status,
      ReplicationTaskStats.CDCLatencySource,
      ReplicationTaskStats.CDCLatencyTarget,
      ReplicationTaskStats.CDCIncomingChanges,
      ReplicationTaskStats.CDCChangesNotApplied
    ]' \
    --output table
}

# DMS 데이터 검증 실패 항목 조회
list_dms_validation_failures() {
  local task_arn="${1:?task_arn 필요}"
  aws dms describe-table-statistics \
    --region "$REGION" \
    --replication-task-arn "$task_arn" \
    --filters Name=validation-state,Values=Error \
    --query 'TableStatistics[].[SchemaName, TableName, ValidationState, ValidationSuspendedRecords]' \
    --output table
}

# DMS Task 테이블별 진행 상태
get_dms_table_stats() {
  local task_arn="${1:?task_arn 필요}"
  aws dms describe-table-statistics \
    --region "$REGION" \
    --replication-task-arn "$task_arn" \
    --query 'TableStatistics[].[
      SchemaName,
      TableName,
      TableState,
      FullLoadRows,
      InsertCount,
      UpdateCount,
      DeleteCount,
      ValidationState
    ]' \
    --output table
}

# ─── DataSync (스토리지 마이그레이션) ──────────────────────────────────────────

# DataSync 에이전트 목록
list_datasync_agents() {
  aws datasync list-agents \
    --region "$REGION" \
    --query 'Agents[].[AgentArn, Name, Status]' \
    --output table
}

# DataSync Task 목록
list_datasync_tasks() {
  aws datasync list-tasks \
    --region "$REGION" \
    --query 'Tasks[].[TaskArn, Name, Status]' \
    --output table
}

# DataSync Task 상태 상세
get_datasync_task() {
  local task_arn="${1:?task_arn 필요}"
  aws datasync describe-task \
    --region "$REGION" \
    --task-arn "$task_arn" \
    --query '{
      Name: Name,
      Status: Status,
      SourceLocation: SourceLocationArn,
      DestinationLocation: DestinationLocationArn,
      Options: Options
    }' \
    --output json
}

# DataSync Task 실행 시작
start_datasync_task() {
  local task_arn="${1:?task_arn 필요}"
  aws datasync start-task-execution \
    --region "$REGION" \
    --task-arn "$task_arn" \
    --query 'TaskExecutionArn' \
    --output text
}

# DataSync 실행 내역 목록
list_datasync_executions() {
  local task_arn="${1:?task_arn 필요}"
  aws datasync list-task-executions \
    --region "$REGION" \
    --task-arn "$task_arn" \
    --query 'TaskExecutions[].[TaskExecutionArn, Status]' \
    --output table
}

# DataSync 실행 상세 (전송 바이트, 파일 수)
get_datasync_execution() {
  local execution_arn="${1:?execution_arn 필요}"
  aws datasync describe-task-execution \
    --region "$REGION" \
    --task-execution-arn "$execution_arn" \
    --query '{
      Status: Status,
      FilesTransferred: FilesTransferred,
      BytesTransferred: BytesTransferred,
      FilesVerified: FilesVerified,
      EstimatedBytesToTransfer: EstimatedBytesToTransfer,
      StartTime: StartTime,
      Result: Result
    }' \
    --output json
}

# ─── Cutover 지원: Route53 TTL 단축 ──────────────────────────────────────────

# Cutover 전 TTL 단축 (기존 레코드 TTL → 60초)
lower_route53_ttl() {
  local hosted_zone_id="${1:?hosted_zone_id 필요}"
  local record_name="${2:?record_name 필요 (예: api.example.com)}"
  local record_type="${3:-A}"

  current=$(aws route53 list-resource-record-sets \
    --hosted-zone-id "$hosted_zone_id" \
    --query "ResourceRecordSets[?Name=='${record_name}.' && Type=='${record_type}']" \
    --output json)

  echo "현재 레코드:"
  echo "$current" | jq '.[0] | {Name, Type, TTL, Records: .ResourceRecords}'
}

# Route53 레코드 IP 변경 (온프레미스 → AWS EC2)
update_route53_record() {
  local hosted_zone_id="${1:?hosted_zone_id 필요}"
  local record_name="${2:?record_name 필요}"
  local new_ip="${3:?new_ip 필요}"
  local record_type="${4:-A}"
  local ttl="${5:-60}"

  aws route53 change-resource-record-sets \
    --hosted-zone-id "$hosted_zone_id" \
    --change-batch "{
      \"Changes\": [{
        \"Action\": \"UPSERT\",
        \"ResourceRecordSet\": {
          \"Name\": \"${record_name}\",
          \"Type\": \"${record_type}\",
          \"TTL\": ${ttl},
          \"ResourceRecords\": [{\"Value\": \"${new_ip}\"}]
        }
      }]
    }" \
    --query 'ChangeInfo.[Status, Comment]' \
    --output table
}

# ─── 마이그레이션 후 최적화 ──────────────────────────────────────────────────

# Compute Optimizer 추천 조회 (EC2)
get_compute_optimizer_ec2() {
  aws compute-optimizer get-ec2-instance-recommendations \
    --region "$REGION" \
    --query 'instanceRecommendations[].[
      instanceName,
      currentInstanceType,
      finding,
      recommendationOptions[0].instanceType,
      recommendationOptions[0].performanceRisk
    ]' \
    --output table
}

# EBS gp2 볼륨 목록 (gp3 전환 대상)
find_gp2_volumes_for_migration() {
  aws ec2 describe-volumes \
    --region "$REGION" \
    --filters "Name=volume-type,Values=gp2" \
    --query 'Volumes[].[VolumeId, Size, Iops, State, Tags[?Key==`Name`].Value | [0]]' \
    --output table
}

# gp2 → gp3 전환
migrate_volume_gp2_to_gp3() {
  local volume_id="${1:?volume_id 필요}"
  aws ec2 modify-volume \
    --region "$REGION" \
    --volume-id "$volume_id" \
    --volume-type gp3 \
    --query 'VolumeModification.[VolumeId, ModificationState, TargetVolumeType]' \
    --output table
}

# ─── 실행 진입점 ──────────────────────────────────────────────────────────────
case "${1:-}" in
  # ADS
  ads-servers)         list_discovered_servers ;;
  ads-export-start)    start_export_network_connections ;;
  ads-export-status)   get_export_status "$2" ;;
  # Migration Hub
  hub-home-set)        set_migration_hub_home_region ;;
  hub-home)            get_migration_hub_home_region ;;
  hub-tasks)           list_migration_tasks ;;
  # MGN
  mgn-servers)         list_mgn_source_servers ;;
  mgn-server)          get_mgn_source_server "$2" ;;
  mgn-launch-config)   get_mgn_launch_config "$2" ;;
  mgn-test)            start_mgn_test "$2" ;;
  mgn-cutover)         start_mgn_cutover "$2" ;;
  mgn-job)             get_mgn_job_status "$2" ;;
  mgn-summary)         summary_mgn_replication ;;
  # DMS
  dms-instances)       list_dms_replication_instances ;;
  dms-endpoints)       list_dms_endpoints ;;
  dms-test-ep)         test_dms_endpoint "$2" "$3" ;;
  dms-tasks)           list_dms_tasks ;;
  dms-start)           start_dms_task "$2" "$3" ;;
  dms-stop)            stop_dms_task "$2" ;;
  dms-lag)             check_dms_cdc_lag ;;
  dms-validate)        list_dms_validation_failures "$2" ;;
  dms-table-stats)     get_dms_table_stats "$2" ;;
  # DataSync
  sync-agents)         list_datasync_agents ;;
  sync-tasks)          list_datasync_tasks ;;
  sync-task)           get_datasync_task "$2" ;;
  sync-start)          start_datasync_task "$2" ;;
  sync-executions)     list_datasync_executions "$2" ;;
  sync-execution)      get_datasync_execution "$2" ;;
  # Cutover
  r53-ttl)             lower_route53_ttl "$2" "$3" "$4" ;;
  r53-update)          update_route53_record "$2" "$3" "$4" "$5" "$6" ;;
  # 최적화
  optimizer)           get_compute_optimizer_ec2 ;;
  gp2-volumes)         find_gp2_volumes_for_migration ;;
  gp2-to-gp3)         migrate_volume_gp2_to_gp3 "$2" ;;
  *)
    echo "사용법: $0 <명령> [인수]"
    echo ""
    echo "  [Application Discovery Service]"
    echo "  ads-servers              탐지된 온프레미스 서버 목록"
    echo "  ads-export-start         네트워크 연결 관계 내보내기 시작"
    echo "  ads-export-status ID     내보내기 상태 확인"
    echo ""
    echo "  [Migration Hub]"
    echo "  hub-home-set             홈 리전 설정 (최초 1회)"
    echo "  hub-home                 홈 리전 확인"
    echo "  hub-tasks                마이그레이션 Task 목록"
    echo ""
    echo "  [MGN — Rehost]"
    echo "  mgn-summary              전체 복제 상태 요약"
    echo "  mgn-servers              소스 서버 목록"
    echo "  mgn-server SERVER_ID     특정 서버 상세"
    echo "  mgn-launch-config ID     Launch Configuration 조회"
    echo "  mgn-test SERVER_ID       테스트 실행"
    echo "  mgn-cutover SERVER_ID    Cutover 실행 (확인 필요)"
    echo "  mgn-job JOB_ID           Job 상태 확인"
    echo ""
    echo "  [DMS — Database]"
    echo "  dms-instances            Replication Instance 목록"
    echo "  dms-endpoints            Endpoint 목록"
    echo "  dms-test-ep RI_ARN EP_ARN  Endpoint 연결 테스트"
    echo "  dms-tasks                Task 목록 및 상태"
    echo "  dms-start TASK_ARN       Task 시작"
    echo "  dms-stop TASK_ARN        Task 중지"
    echo "  dms-lag                  CDC Lag 전체 확인"
    echo "  dms-validate TASK_ARN    데이터 검증 실패 항목"
    echo "  dms-table-stats TASK_ARN 테이블별 진행 상태"
    echo ""
    echo "  [DataSync — Storage]"
    echo "  sync-agents              DataSync 에이전트 목록"
    echo "  sync-tasks               Task 목록"
    echo "  sync-task TASK_ARN       Task 상세"
    echo "  sync-start TASK_ARN      Task 실행"
    echo "  sync-executions TASK_ARN 실행 내역"
    echo "  sync-execution EXEC_ARN  실행 상세 (전송량)"
    echo ""
    echo "  [Cutover]"
    echo "  r53-ttl ZONE_ID NAME     Route53 레코드 현재 TTL 확인"
    echo "  r53-update ZONE_ID NAME NEW_IP  DNS 전환"
    echo ""
    echo "  [마이그레이션 후 최적화]"
    echo "  optimizer                Compute Optimizer EC2 추천"
    echo "  gp2-volumes              gp3 전환 대상 gp2 볼륨"
    echo "  gp2-to-gp3 VOLUME_ID    gp2 → gp3 전환"
    ;;
esac
