"""
RDS / Aurora 실무 boto3 쿼리 모음
사용법: python rds_queries.py <명령> [인수]
"""

import boto3
import sys
import json
from datetime import datetime, timezone, timedelta

session = boto3.Session(region_name="ap-northeast-2")
rds = session.client("rds")
cw = session.client("cloudwatch")


# ─── RDS 인스턴스 조회 ─────────────────────────────────────────────────────────

def list_rds_instances() -> list[dict]:
    """모든 RDS 인스턴스 목록"""
    paginator = rds.get_paginator("describe_db_instances")
    instances = []

    for page in paginator.paginate():
        for db in page["DBInstances"]:
            instances.append({
                "db_id": db["DBInstanceIdentifier"],
                "class": db["DBInstanceClass"],
                "engine": db["Engine"],
                "version": db["EngineVersion"],
                "status": db["DBInstanceStatus"],
                "multi_az": db["MultiAZ"],
                "endpoint": db.get("Endpoint", {}).get("Address", "-"),
            })

    return instances


def describe_rds_instance(db_id: str) -> dict:
    """특정 RDS 인스턴스 상세 정보"""
    resp = rds.describe_db_instances(DBInstanceIdentifier=db_id)
    db = resp["DBInstances"][0]

    return {
        "db_id": db["DBInstanceIdentifier"],
        "class": db["DBInstanceClass"],
        "engine": f"{db['Engine']} {db['EngineVersion']}",
        "status": db["DBInstanceStatus"],
        "multi_az": db["MultiAZ"],
        "storage_type": db["StorageType"],
        "allocated_storage_gb": db["AllocatedStorage"],
        "endpoint": db.get("Endpoint", {}).get("Address", "-"),
        "port": db.get("Endpoint", {}).get("Port", "-"),
        "vpc_id": db.get("DBSubnetGroup", {}).get("VpcId", "-"),
        "subnet_group": db.get("DBSubnetGroup", {}).get("DBSubnetGroupName", "-"),
        "parameter_group": db["DBParameterGroups"][0]["DBParameterGroupName"] if db.get("DBParameterGroups") else "-",
        "backup_retention_days": db["BackupRetentionPeriod"],
        "deletion_protection": db["DeletionProtection"],
        "publicly_accessible": db["PubliclyAccessible"],
        "ca_cert": db.get("CACertificateIdentifier", "-"),
    }


# ─── Aurora 클러스터 ────────────────────────────────────────────────────────────

def list_aurora_clusters() -> list[dict]:
    """Aurora 클러스터 목록"""
    paginator = rds.get_paginator("describe_db_clusters")
    clusters = []

    for page in paginator.paginate():
        for c in page["DBClusters"]:
            clusters.append({
                "cluster_id": c["DBClusterIdentifier"],
                "engine": c["Engine"],
                "version": c["EngineVersion"],
                "status": c["Status"],
                "db_name": c.get("DatabaseName", "-"),
                "writer_endpoint": c.get("Endpoint", "-"),
                "reader_endpoint": c.get("ReaderEndpoint", "-"),
                "multi_az": c["MultiAZ"],
            })

    return clusters


def list_cluster_members(cluster_id: str) -> list[dict]:
    """Aurora 클러스터 멤버 (Writer/Reader 구분)"""
    resp = rds.describe_db_clusters(DBClusterIdentifier=cluster_id)
    members = resp["DBClusters"][0]["DBClusterMembers"]

    return [
        {
            "db_id": m["DBInstanceIdentifier"],
            "role": "Writer" if m["IsClusterWriter"] else "Reader",
            "param_group_status": m["DBClusterParameterGroupStatus"],
        }
        for m in members
    ]


# ─── 파라미터 그룹 ─────────────────────────────────────────────────────────────

def list_parameter_groups() -> list[dict]:
    """파라미터 그룹 목록"""
    paginator = rds.get_paginator("describe_db_parameter_groups")
    groups = []

    for page in paginator.paginate():
        for pg in page["DBParameterGroups"]:
            groups.append({
                "name": pg["DBParameterGroupName"],
                "family": pg["DBParameterGroupFamily"],
                "description": pg["Description"],
            })

    return groups


def list_modified_parameters(pg_name: str) -> list[dict]:
    """파라미터 그룹의 non-default(user 수정) 파라미터만 조회"""
    paginator = rds.get_paginator("describe_db_parameters")
    params = []

    for page in paginator.paginate(DBParameterGroupName=pg_name, Source="user"):
        for p in page["Parameters"]:
            params.append({
                "name": p["ParameterName"],
                "value": p.get("ParameterValue", "-"),
                "apply_method": p.get("ApplyMethod", "-"),
                "apply_type": p.get("ApplyType", "-"),
            })

    return params


# ─── 스냅샷 ────────────────────────────────────────────────────────────────────

def list_automated_snapshots(db_id: str) -> list[dict]:
    """특정 인스턴스의 자동 스냅샷 목록"""
    paginator = rds.get_paginator("describe_db_snapshots")
    snapshots = []

    for page in paginator.paginate(DBInstanceIdentifier=db_id, SnapshotType="automated"):
        for s in page["DBSnapshots"]:
            snapshots.append({
                "snapshot_id": s["DBSnapshotIdentifier"],
                "created_at": s["SnapshotCreateTime"].isoformat() if s.get("SnapshotCreateTime") else "-",
                "storage_gb": s["AllocatedStorage"],
                "status": s["Status"],
                "engine": f"{s['Engine']} {s['EngineVersion']}",
            })

    return sorted(snapshots, key=lambda x: x["created_at"], reverse=True)


def list_manual_snapshots() -> list[dict]:
    """수동 스냅샷 전체 목록 (계정 내 모든 인스턴스)"""
    paginator = rds.get_paginator("describe_db_snapshots")
    snapshots = []

    for page in paginator.paginate(SnapshotType="manual"):
        for s in page["DBSnapshots"]:
            snapshots.append({
                "snapshot_id": s["DBSnapshotIdentifier"],
                "db_id": s["DBInstanceIdentifier"],
                "created_at": s["SnapshotCreateTime"].isoformat() if s.get("SnapshotCreateTime") else "-",
                "storage_gb": s["AllocatedStorage"],
                "status": s["Status"],
            })

    return sorted(snapshots, key=lambda x: x["created_at"], reverse=True)


# ─── 유지보수 / 이벤트 ─────────────────────────────────────────────────────────

def list_pending_maintenance() -> list[dict]:
    """대기 중인 유지보수 작업 확인"""
    paginator = rds.get_paginator("describe_pending_maintenance_actions")
    actions = []

    for page in paginator.paginate():
        for item in page["PendingMaintenanceActions"]:
            for detail in item.get("PendingMaintenanceActionDetails", []):
                actions.append({
                    "resource": item["ResourceIdentifier"],
                    "action": detail["Action"],
                    "auto_apply_after": str(detail.get("AutoAppliedAfterDate", "-")),
                    "forced_apply": str(detail.get("ForcedApplyDate", "-")),
                    "description": detail.get("Description", "-"),
                })

    return actions


def list_recent_events(hours: int = 24) -> list[dict]:
    """최근 N시간 RDS 이벤트 조회"""
    start_time = datetime.now(timezone.utc) - timedelta(hours=hours)
    paginator = rds.get_paginator("describe_events")
    events = []

    for page in paginator.paginate(StartTime=start_time):
        for e in page["Events"]:
            events.append({
                "source": e["SourceIdentifier"],
                "source_type": e["SourceType"],
                "message": e["Message"],
                "date": e["Date"].isoformat(),
            })

    return sorted(events, key=lambda x: x["date"], reverse=True)


# ─── 모니터링 (CloudWatch) ──────────────────────────────────────────────────────

def _get_rds_metric(db_id: str, metric_name: str, period: int = 300, stat: str = "Average", hours: int = 1) -> list[dict]:
    """RDS CloudWatch 지표 공통 조회"""
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(hours=hours)

    resp = cw.get_metric_statistics(
        Namespace="AWS/RDS",
        MetricName=metric_name,
        Dimensions=[{"Name": "DBInstanceIdentifier", "Value": db_id}],
        StartTime=start_time,
        EndTime=end_time,
        Period=period,
        Statistics=[stat],
    )

    return sorted(
        [{"timestamp": dp["Timestamp"].isoformat(), "value": round(dp[stat], 4)} for dp in resp["Datapoints"]],
        key=lambda x: x["timestamp"],
    )


def get_cpu_utilization(db_id: str) -> list[dict]:
    """CPU 사용률 최근 1시간 (5분 평균, %)"""
    return _get_rds_metric(db_id, "CPUUtilization")


def get_freeable_memory(db_id: str) -> list[dict]:
    """가용 메모리 최근 1시간 (bytes → MB 변환)"""
    raw = _get_rds_metric(db_id, "FreeableMemory")
    for dp in raw:
        dp["value_mb"] = round(dp["value"] / 1024 / 1024, 1)
    return raw


def get_connection_count(db_id: str) -> list[dict]:
    """연결 수 최근 1시간 (1분 최대값)"""
    return _get_rds_metric(db_id, "DatabaseConnections", period=60, stat="Maximum")


def get_read_write_iops(db_id: str) -> list[dict]:
    """Read/Write IOPS 최근 1시간 비교"""
    read_iops = _get_rds_metric(db_id, "ReadIOPS")
    write_iops = {dp["timestamp"]: dp["value"] for dp in _get_rds_metric(db_id, "WriteIOPS")}

    return [
        {
            "timestamp": dp["timestamp"],
            "read_iops": dp["value"],
            "write_iops": write_iops.get(dp["timestamp"], 0),
        }
        for dp in read_iops
    ]


# ─── 페일오버 ──────────────────────────────────────────────────────────────────

def failover_aurora_cluster(cluster_id: str, target_instance: str = "") -> dict:
    """Aurora 클러스터 수동 페일오버 (Writer → Reader 전환)"""
    confirm = input(f"[주의] Aurora 클러스터 페일오버: {cluster_id}\n계속하시겠습니까? (yes/no): ")
    if confirm.strip().lower() != "yes":
        return {"status": "cancelled"}

    kwargs = {"DBClusterIdentifier": cluster_id}
    if target_instance:
        kwargs["TargetDBInstanceIdentifier"] = target_instance

    rds.failover_db_cluster(**kwargs)

    resp = rds.describe_db_clusters(DBClusterIdentifier=cluster_id)
    c = resp["DBClusters"][0]
    return {
        "cluster_id": c["DBClusterIdentifier"],
        "status": c["Status"],
        "message": "페일오버 요청 완료",
    }


# ─── CLI 실행 ──────────────────────────────────────────────────────────────────

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
    "list":             (list_rds_instances,     "RDS 인스턴스 전체 목록"),
    "clusters":         (list_aurora_clusters,   "Aurora 클러스터 목록"),
    "param-groups":     (list_parameter_groups,  "파라미터 그룹 목록"),
    "manual-snapshots": (list_manual_snapshots,  "수동 스냅샷 전체 목록"),
    "maintenance":      (list_pending_maintenance, "대기 중인 유지보수 작업"),
}

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""

    if cmd == "describe" and len(sys.argv) >= 3:
        print(json.dumps(describe_rds_instance(sys.argv[2]), indent=2, ensure_ascii=False))
    elif cmd == "members" and len(sys.argv) >= 3:
        print_table(list_cluster_members(sys.argv[2]))
    elif cmd == "params" and len(sys.argv) >= 3:
        print_table(list_modified_parameters(sys.argv[2]))
    elif cmd == "auto-snapshots" and len(sys.argv) >= 3:
        print_table(list_automated_snapshots(sys.argv[2]))
    elif cmd == "events":
        hours = int(sys.argv[2]) if len(sys.argv) >= 3 else 24
        print_table(list_recent_events(hours))
    elif cmd == "cpu" and len(sys.argv) >= 3:
        print_table(get_cpu_utilization(sys.argv[2]))
    elif cmd == "memory" and len(sys.argv) >= 3:
        print_table(get_freeable_memory(sys.argv[2]))
    elif cmd == "connections" and len(sys.argv) >= 3:
        print_table(get_connection_count(sys.argv[2]))
    elif cmd == "iops" and len(sys.argv) >= 3:
        print_table(get_read_write_iops(sys.argv[2]))
    elif cmd == "failover" and len(sys.argv) >= 3:
        target = sys.argv[3] if len(sys.argv) >= 4 else ""
        print(json.dumps(failover_aurora_cluster(sys.argv[2], target), indent=2, ensure_ascii=False))
    elif cmd in COMMANDS:
        print_table(COMMANDS[cmd][0]())
    else:
        print("사용법: python rds_queries.py <명령> [인수]\n")
        for k, (_, desc) in COMMANDS.items():
            print(f"  {k:<20} {desc}")
        print("  describe DB_ID             RDS 인스턴스 상세")
        print("  members CLUSTER_ID         Aurora Writer/Reader 구성")
        print("  params PG_NAME             수정된 파라미터 조회")
        print("  auto-snapshots DB_ID       자동 스냅샷 목록")
        print("  events [HOURS]             최근 이벤트 (기본: 24시간)")
        print("  cpu DB_ID                  CPU 사용률 (최근 1시간)")
        print("  memory DB_ID               가용 메모리 (최근 1시간)")
        print("  connections DB_ID          연결 수 (최근 1시간)")
        print("  iops DB_ID                 Read/Write IOPS 비교")
        print("  failover CLUSTER_ID [TARGET_INSTANCE]  Aurora 수동 페일오버")
