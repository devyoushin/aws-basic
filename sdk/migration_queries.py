"""
마이그레이션 실무 boto3 쿼리 모음 (MGN, DMS, DataSync, Migration Hub, ADS)
사용법: python migration_queries.py <명령> [인수]
"""

import boto3
import sys
import json
from datetime import datetime, timezone
from typing import Optional

session = boto3.Session(region_name="ap-northeast-2")

mgn = session.client("mgn")
dms = session.client("dms")
datasync = session.client("datasync")
discovery = session.client("discovery")
migrationhub = session.client("migrationhub", region_name="us-west-2")  # Migration Hub는 us-west-2 고정
route53 = session.client("route53")
ec2 = session.client("ec2")
compute_optimizer = session.client("compute-optimizer")


# ─── Application Discovery Service (ADS) ──────────────────────────────────────

def list_discovered_agents() -> list[dict]:
    """탐지된 온프레미스 서버(에이전트) 목록"""
    resp = discovery.describe_agents()
    return [
        {
            "agent_id": a["agentId"],
            "hostname": a.get("hostName", "-"),
            "agent_type": a["agentType"],
            "status": a["health"],
            "ip": a.get("agentNetworkInfoList", [{}])[0].get("ipAddress", "-"),
            "os": a.get("osName", "-"),
        }
        for a in resp.get("agents", [])
    ]


def start_ads_export() -> str:
    """서버 정보 내보내기 시작 → exportId 반환"""
    resp = discovery.start_export_task(
        filters=[
            {"name": "resourceType", "condition": "EQUALS", "values": ["SERVER"]}
        ]
    )
    return resp["exportId"]


def get_ads_export_status(export_id: str) -> dict:
    """내보내기 작업 상태 확인"""
    resp = discovery.describe_export_tasks(exportIds=[export_id])
    info = resp["exportsInfo"][0]
    return {
        "export_id": info["exportId"],
        "status": info["exportStatus"],
        "message": info.get("statusMessage", "-"),
        "s3_url": info.get("configurationsDownloadUrl", "-"),
        "requested_at": info.get("exportRequestTime", "-"),
    }


# ─── Application Migration Service (MGN) — Rehost ─────────────────────────────

def list_mgn_source_servers() -> list[dict]:
    """소스 서버 목록 (복제 상태 포함)"""
    resp = mgn.describe_source_servers(filters={})
    result = []
    for s in resp.get("items", []):
        rep = s.get("dataReplicationInfo", {})
        result.append({
            "server_id": s["sourceServerID"],
            "hostname": s.get("sourceProperties", {}).get("identificationHints", {}).get("hostname", "-"),
            "replication_state": rep.get("dataReplicationState", "-"),
            "lag": rep.get("lagDuration", "-"),
            "eta": rep.get("etaDateTime", "-"),
            "lifecycle": s.get("lifeCycle", {}).get("state", "-"),
        })
    return result


def get_mgn_source_server_detail(server_id: str) -> dict:
    """특정 소스 서버 상세 정보"""
    resp = mgn.describe_source_servers(filters={"sourceServerIDs": [server_id]})
    if not resp["items"]:
        return {"error": f"서버를 찾을 수 없음: {server_id}"}

    s = resp["items"][0]
    props = s.get("sourceProperties", {})
    rep = s.get("dataReplicationInfo", {})

    return {
        "server_id": s["sourceServerID"],
        "hostname": props.get("identificationHints", {}).get("hostname", "-"),
        "os": props.get("os", {}).get("fullString", "-"),
        "cpu": props.get("cpus", [{}])[0].get("modelName", "-"),
        "ram_bytes": props.get("ramBytes", 0),
        "disks": [
            {"device": d.get("deviceName"), "bytes": d.get("bytes")}
            for d in props.get("disks", [])
        ],
        "replication_state": rep.get("dataReplicationState", "-"),
        "lag_duration": rep.get("lagDuration", "-"),
        "lifecycle_state": s.get("lifeCycle", {}).get("state", "-"),
        "tags": s.get("tags", {}),
    }


def get_mgn_launch_configuration(server_id: str) -> dict:
    """MGN Launch Configuration 조회"""
    resp = mgn.get_launch_configuration(sourceServerID=server_id)
    data = resp.get("ec2LaunchTemplateData", {})
    return {
        "name": resp.get("name"),
        "instance_type": data.get("instanceType", "-"),
        "launch_disposition": resp.get("launchDisposition", "-"),
        "right_sizing": resp.get("targetInstanceTypeRightSizingMethod", "-"),
        "copy_tags": resp.get("copyTags", False),
    }


def start_mgn_test(server_id: str) -> str:
    """MGN 테스트 실행 → jobID 반환"""
    resp = mgn.start_test(sourceServerIDs=[server_id])
    return resp["job"]["jobID"]


def start_mgn_cutover(server_id: str) -> str:
    """MGN Cutover 실행 → jobID 반환 (호출 전 반드시 확인)"""
    resp = mgn.start_cutover(sourceServerIDs=[server_id])
    return resp["job"]["jobID"]


def get_mgn_job_status(job_id: str) -> dict:
    """MGN Job 상태 확인"""
    resp = mgn.describe_jobs(filters={"jobIDs": [job_id]})
    if not resp["items"]:
        return {"error": f"Job 없음: {job_id}"}
    j = resp["items"][0]
    return {
        "job_id": j["jobID"],
        "status": j["status"],
        "type": j["type"],
        "initiated_by": j.get("initiatedBy", "-"),
        "creation_date": j.get("creationDateTime", "-"),
        "end_date": j.get("endDateTime", "-"),
    }


# ─── Database Migration Service (DMS) ─────────────────────────────────────────

def list_dms_replication_instances() -> list[dict]:
    """DMS Replication Instance 목록"""
    resp = dms.describe_replication_instances()
    return [
        {
            "identifier": r["ReplicationInstanceIdentifier"],
            "class": r["ReplicationInstanceClass"],
            "status": r["ReplicationInstanceStatus"],
            "multi_az": r["MultiAZ"],
            "public": r["PubliclyAccessible"],
            "storage_gb": r["AllocatedStorage"],
            "engine_version": r["EngineVersion"],
        }
        for r in resp.get("ReplicationInstances", [])
    ]


def list_dms_endpoints() -> list[dict]:
    """DMS Endpoint 목록"""
    resp = dms.describe_endpoints()
    return [
        {
            "identifier": e["EndpointIdentifier"],
            "type": e["EndpointType"],
            "engine": e["EngineName"],
            "status": e["Status"],
            "server": e.get("ServerName", "-"),
            "database": e.get("DatabaseName", "-"),
        }
        for e in resp.get("Endpoints", [])
    ]


def test_dms_connection(replication_instance_arn: str, endpoint_arn: str) -> dict:
    """DMS Endpoint 연결 테스트"""
    resp = dms.test_connection(
        ReplicationInstanceArn=replication_instance_arn,
        EndpointArn=endpoint_arn,
    )
    conn = resp["Connection"]
    return {
        "endpoint_id": conn["EndpointIdentifier"],
        "replication_instance_id": conn["ReplicationInstanceIdentifier"],
        "status": conn["Status"],
        "last_failure": conn.get("LastFailureMessage", "-"),
    }


def list_dms_tasks() -> list[dict]:
    """DMS 마이그레이션 Task 목록 및 상태"""
    resp = dms.describe_replication_tasks()
    result = []
    for t in resp.get("ReplicationTasks", []):
        stats = t.get("ReplicationTaskStats", {})
        result.append({
            "identifier": t["ReplicationTaskIdentifier"],
            "status": t["Status"],
            "migration_type": t["MigrationType"],
            "full_load_pct": stats.get("FullLoadProgressPercent", 0),
            "cdc_lag_source": stats.get("CDCLatencySource", 0),
            "cdc_lag_target": stats.get("CDCLatencyTarget", 0),
            "start_date": t.get("ReplicationTaskStartDate", "-"),
        })
    return result


def start_dms_task(task_arn: str, start_type: str = "start-replication") -> str:
    """DMS Task 시작 → 상태 반환
    start_type: start-replication | resume-processing | reload-target
    """
    resp = dms.start_replication_task(
        ReplicationTaskArn=task_arn,
        StartReplicationTaskType=start_type,
    )
    return resp["ReplicationTask"]["Status"]


def stop_dms_task(task_arn: str) -> str:
    """DMS Task 중지"""
    resp = dms.stop_replication_task(ReplicationTaskArn=task_arn)
    return resp["ReplicationTask"]["Status"]


def check_dms_cdc_lag(task_arn: Optional[str] = None) -> list[dict]:
    """CDC Lag 확인 (Cutover 판단 기준: Lag < 60초)"""
    kwargs = {}
    if task_arn:
        kwargs["Filters"] = [{"Name": "replication-task-arn", "Values": [task_arn]}]
    resp = dms.describe_replication_tasks(**kwargs)
    result = []
    for t in resp.get("ReplicationTasks", []):
        stats = t.get("ReplicationTaskStats", {})
        lag_src = stats.get("CDCLatencySource", 0)
        lag_tgt = stats.get("CDCLatencyTarget", 0)
        result.append({
            "identifier": t["ReplicationTaskIdentifier"],
            "status": t["Status"],
            "cdc_lag_source_sec": lag_src,
            "cdc_lag_target_sec": lag_tgt,
            "cutover_ready": "✅ 가능" if max(lag_src, lag_tgt) < 60 else "❌ 대기",
        })
    return result


def get_dms_table_statistics(task_arn: str) -> list[dict]:
    """테이블별 마이그레이션 진행 상태"""
    resp = dms.describe_table_statistics(ReplicationTaskArn=task_arn)
    return [
        {
            "schema": t["SchemaName"],
            "table": t["TableName"],
            "state": t["TableState"],
            "full_load_rows": t.get("FullLoadRows", 0),
            "inserts": t.get("Inserts", 0),
            "updates": t.get("Updates", 0),
            "deletes": t.get("Deletes", 0),
            "validation": t.get("ValidationState", "-"),
        }
        for t in resp.get("TableStatistics", [])
    ]


def get_dms_validation_failures(task_arn: str) -> list[dict]:
    """데이터 검증 실패 테이블 목록 (Cutover 전 반드시 0건 확인)"""
    resp = dms.describe_table_statistics(
        ReplicationTaskArn=task_arn,
        Filters=[{"Name": "validation-state", "Values": ["Error", "Mismatched Records"]}],
    )
    failures = [t for t in resp.get("TableStatistics", []) if t.get("ValidationState") not in ("Not enabled", "Validated")]
    return [
        {
            "schema": t["SchemaName"],
            "table": t["TableName"],
            "validation_state": t.get("ValidationState", "-"),
            "suspended_records": t.get("ValidationSuspendedRecords", 0),
            "pending_records": t.get("ValidationPendingRecords", 0),
            "failed_records": t.get("ValidationFailedRecords", 0),
        }
        for t in failures
    ]


# ─── DataSync (스토리지 마이그레이션) ────────────────────────────────────────

def list_datasync_agents() -> list[dict]:
    """DataSync 에이전트 목록"""
    resp = datasync.list_agents()
    result = []
    for a in resp.get("Agents", []):
        detail = datasync.describe_agent(AgentArn=a["AgentArn"])
        result.append({
            "name": a.get("Name", "-"),
            "arn": a["AgentArn"],
            "status": detail["Status"],
            "endpoint_type": detail.get("EndpointType", "-"),
        })
    return result


def list_datasync_tasks() -> list[dict]:
    """DataSync Task 목록"""
    resp = datasync.list_tasks()
    result = []
    for t in resp.get("Tasks", []):
        detail = datasync.describe_task(TaskArn=t["TaskArn"])
        result.append({
            "name": t.get("Name", "-"),
            "arn": t["TaskArn"],
            "status": detail["Status"],
            "source_arn": detail.get("SourceLocationArn", "-"),
            "dest_arn": detail.get("DestinationLocationArn", "-"),
        })
    return result


def start_datasync_task(task_arn: str) -> str:
    """DataSync Task 실행 → TaskExecutionArn 반환"""
    resp = datasync.start_task_execution(TaskArn=task_arn)
    return resp["TaskExecutionArn"]


def get_datasync_execution(execution_arn: str) -> dict:
    """DataSync 실행 상세 (전송량, 파일 수)"""
    resp = datasync.describe_task_execution(TaskExecutionArn=execution_arn)
    transferred = resp.get("BytesTransferred", 0)
    estimated = resp.get("EstimatedBytesToTransfer", 0)
    pct = round(transferred / estimated * 100, 1) if estimated else 0

    return {
        "status": resp["Status"],
        "files_transferred": resp.get("FilesTransferred", 0),
        "bytes_transferred_gb": round(transferred / (1024**3), 2),
        "estimated_gb": round(estimated / (1024**3), 2),
        "progress_pct": pct,
        "files_verified": resp.get("FilesVerified", 0),
        "start_time": str(resp.get("StartTime", "-")),
        "result": resp.get("Result", {}),
    }


# ─── Cutover: Route53 DNS 전환 ─────────────────────────────────────────────────

def get_route53_record(hosted_zone_id: str, record_name: str, record_type: str = "A") -> Optional[dict]:
    """Route53 레코드 조회"""
    if not record_name.endswith("."):
        record_name += "."
    resp = route53.list_resource_record_sets(HostedZoneId=hosted_zone_id)
    for r in resp.get("ResourceRecordSets", []):
        if r["Name"] == record_name and r["Type"] == record_type:
            return {
                "name": r["Name"],
                "type": r["Type"],
                "ttl": r.get("TTL", "-"),
                "values": [v["Value"] for v in r.get("ResourceRecords", [])],
            }
    return None


def update_route53_record(
    hosted_zone_id: str,
    record_name: str,
    new_ip: str,
    record_type: str = "A",
    ttl: int = 60,
) -> dict:
    """Route53 레코드 변경 (Cutover 시 DNS 전환)"""
    if not record_name.endswith("."):
        record_name += "."
    resp = route53.change_resource_record_sets(
        HostedZoneId=hosted_zone_id,
        ChangeBatch={
            "Comment": f"Migration cutover: {datetime.now(timezone.utc).isoformat()}",
            "Changes": [
                {
                    "Action": "UPSERT",
                    "ResourceRecordSet": {
                        "Name": record_name,
                        "Type": record_type,
                        "TTL": ttl,
                        "ResourceRecords": [{"Value": new_ip}],
                    },
                }
            ],
        },
    )
    change = resp["ChangeInfo"]
    return {
        "change_id": change["Id"],
        "status": change["Status"],
        "submitted_at": str(change["SubmittedAt"]),
    }


# ─── 마이그레이션 후 최적화 ────────────────────────────────────────────────────

def get_compute_optimizer_recommendations() -> list[dict]:
    """Compute Optimizer EC2 인스턴스 추천"""
    resp = compute_optimizer.get_ec2_instance_recommendations()
    result = []
    for r in resp.get("instanceRecommendations", []):
        options = r.get("recommendationOptions", [])
        best = options[0] if options else {}
        result.append({
            "instance_name": r.get("instanceName", "-"),
            "current_type": r.get("currentInstanceType", "-"),
            "finding": r.get("finding", "-"),
            "recommended_type": best.get("instanceType", "-"),
            "performance_risk": best.get("performanceRisk", "-"),
            "savings_opportunity_pct": best.get("savingsOpportunity", {}).get("savingsOpportunityPercentage", 0),
        })
    return result


def find_gp2_volumes() -> list[dict]:
    """gp3 전환 대상 gp2 볼륨 목록"""
    paginator = ec2.get_paginator("describe_volumes")
    volumes = []
    for page in paginator.paginate(Filters=[{"Name": "volume-type", "Values": ["gp2"]}]):
        for v in page["Volumes"]:
            name = next((t["Value"] for t in v.get("Tags", []) if t["Key"] == "Name"), "-")
            volumes.append({
                "volume_id": v["VolumeId"],
                "name": name,
                "size_gb": v["Size"],
                "iops": v.get("Iops", 0),
                "state": v["State"],
                "az": v["AvailabilityZone"],
            })
    return volumes


def migrate_gp2_to_gp3(volume_id: str, iops: int = 3000, throughput: int = 125) -> dict:
    """gp2 → gp3 전환 (기본값: 3000 IOPS, 125 MB/s)"""
    resp = ec2.modify_volume(
        VolumeId=volume_id,
        VolumeType="gp3",
        Iops=iops,
        Throughput=throughput,
    )
    mod = resp["VolumeModification"]
    return {
        "volume_id": mod["VolumeId"],
        "state": mod["ModificationState"],
        "target_type": mod["TargetVolumeType"],
        "target_iops": mod["TargetIops"],
        "target_throughput": mod.get("TargetThroughput", "-"),
    }


# ─── 유틸리티 ─────────────────────────────────────────────────────────────────

def print_table(data: list[dict]) -> None:
    if not data:
        print("(결과 없음)")
        return
    keys = list(data[0].keys())
    widths = {k: max(len(k), max(len(str(row.get(k, ""))) for row in data)) for k in keys}
    header = "  ".join(k.ljust(widths[k]) for k in keys)
    print(header)
    print("-" * len(header))
    for row in data:
        print("  ".join(str(row.get(k, "")).ljust(widths[k]) for k in keys))


COMMANDS = {
    # ADS
    "ads-agents": (list_discovered_agents, "탐지된 온프레미스 서버 목록"),
    # MGN
    "mgn-servers": (list_mgn_source_servers, "소스 서버 복제 상태"),
    # DMS
    "dms-instances": (list_dms_replication_instances, "DMS Replication Instance"),
    "dms-endpoints": (list_dms_endpoints, "DMS Endpoint 목록"),
    "dms-tasks": (list_dms_tasks, "DMS Task 목록 및 상태"),
    "dms-lag": (check_dms_cdc_lag, "CDC Lag 전체 확인 (Cutover 판단)"),
    # DataSync
    "sync-agents": (list_datasync_agents, "DataSync 에이전트"),
    "sync-tasks": (list_datasync_tasks, "DataSync Task 목록"),
    # 최적화
    "optimizer": (get_compute_optimizer_recommendations, "Compute Optimizer 추천"),
    "gp2-volumes": (find_gp2_volumes, "gp3 전환 대상 gp2 볼륨"),
}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""

    if cmd == "ads-export-start":
        print(f"Export ID: {start_ads_export()}")

    elif cmd == "ads-export-status" and len(sys.argv) >= 3:
        print(json.dumps(get_ads_export_status(sys.argv[2]), indent=2, ensure_ascii=False))

    elif cmd == "mgn-server" and len(sys.argv) >= 3:
        print(json.dumps(get_mgn_source_server_detail(sys.argv[2]), indent=2, ensure_ascii=False))

    elif cmd == "mgn-launch-config" and len(sys.argv) >= 3:
        print(json.dumps(get_mgn_launch_configuration(sys.argv[2]), indent=2, ensure_ascii=False))

    elif cmd == "mgn-test" and len(sys.argv) >= 3:
        job_id = start_mgn_test(sys.argv[2])
        print(f"테스트 Job ID: {job_id}")

    elif cmd == "mgn-cutover" and len(sys.argv) >= 3:
        confirm = input(f"⚠️  Cutover 실행: {sys.argv[2]}\n계속하려면 'yes' 입력: ")
        if confirm == "yes":
            job_id = start_mgn_cutover(sys.argv[2])
            print(f"Cutover Job ID: {job_id}")
        else:
            print("취소됨")

    elif cmd == "mgn-job" and len(sys.argv) >= 3:
        print(json.dumps(get_mgn_job_status(sys.argv[2]), indent=2, ensure_ascii=False))

    elif cmd == "dms-test-ep" and len(sys.argv) >= 4:
        print(json.dumps(test_dms_connection(sys.argv[2], sys.argv[3]), indent=2, ensure_ascii=False))

    elif cmd == "dms-start" and len(sys.argv) >= 3:
        start_type = sys.argv[3] if len(sys.argv) >= 4 else "start-replication"
        print(f"Status: {start_dms_task(sys.argv[2], start_type)}")

    elif cmd == "dms-stop" and len(sys.argv) >= 3:
        print(f"Status: {stop_dms_task(sys.argv[2])}")

    elif cmd == "dms-lag" and len(sys.argv) >= 3:
        print_table(check_dms_cdc_lag(sys.argv[2]))

    elif cmd == "dms-table-stats" and len(sys.argv) >= 3:
        print_table(get_dms_table_statistics(sys.argv[2]))

    elif cmd == "dms-validate" and len(sys.argv) >= 3:
        data = get_dms_validation_failures(sys.argv[2])
        if not data:
            print("✅ 검증 실패 0건 — Cutover 가능")
        else:
            print(f"❌ 검증 실패 {len(data)}건:")
            print_table(data)

    elif cmd == "sync-start" and len(sys.argv) >= 3:
        print(f"Execution ARN: {start_datasync_task(sys.argv[2])}")

    elif cmd == "sync-execution" and len(sys.argv) >= 3:
        print(json.dumps(get_datasync_execution(sys.argv[2]), indent=2, ensure_ascii=False))

    elif cmd == "r53-record" and len(sys.argv) >= 4:
        record_type = sys.argv[4] if len(sys.argv) >= 5 else "A"
        result = get_route53_record(sys.argv[2], sys.argv[3], record_type)
        print(json.dumps(result, indent=2, ensure_ascii=False) if result else "레코드 없음")

    elif cmd == "r53-update" and len(sys.argv) >= 5:
        record_type = sys.argv[5] if len(sys.argv) >= 6 else "A"
        ttl = int(sys.argv[6]) if len(sys.argv) >= 7 else 60
        print(json.dumps(update_route53_record(sys.argv[2], sys.argv[3], sys.argv[4], record_type, ttl), indent=2, ensure_ascii=False))

    elif cmd == "gp2-to-gp3" and len(sys.argv) >= 3:
        print(json.dumps(migrate_gp2_to_gp3(sys.argv[2]), indent=2, ensure_ascii=False))

    elif cmd in COMMANDS:
        print_table(COMMANDS[cmd][0]())

    else:
        print("사용법: python migration_queries.py <명령> [인수]\n")
        for k, (_, desc) in COMMANDS.items():
            print(f"  {k:<25} {desc}")
        print()
        print("  ads-export-start              ADS 내보내기 시작")
        print("  ads-export-status EXPORT_ID   내보내기 상태 확인")
        print("  mgn-server SERVER_ID          소스 서버 상세")
        print("  mgn-launch-config SERVER_ID   Launch Configuration")
        print("  mgn-test SERVER_ID            테스트 실행")
        print("  mgn-cutover SERVER_ID         Cutover 실행 (확인 필요)")
        print("  mgn-job JOB_ID                Job 상태 확인")
        print("  dms-test-ep RI_ARN EP_ARN     Endpoint 연결 테스트")
        print("  dms-start TASK_ARN [type]     Task 시작")
        print("  dms-stop TASK_ARN             Task 중지")
        print("  dms-lag TASK_ARN              CDC Lag (특정 Task)")
        print("  dms-table-stats TASK_ARN      테이블별 진행 상태")
        print("  dms-validate TASK_ARN         검증 실패 항목 (0건 = Cutover 가능)")
        print("  sync-start TASK_ARN           DataSync Task 실행")
        print("  sync-execution EXEC_ARN       전송 진행률 확인")
        print("  r53-record ZONE_ID NAME       Route53 레코드 조회")
        print("  r53-update ZONE_ID NAME IP    DNS Cutover 전환")
        print("  gp2-to-gp3 VOLUME_ID          gp2 → gp3 전환")
