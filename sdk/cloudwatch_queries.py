"""
CloudWatch 실무 boto3 쿼리 모음
사용법: python cloudwatch_queries.py <명령> [인수]
"""

import boto3
import sys
import json
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

session = boto3.Session(region_name="ap-northeast-2")
cw = session.client("cloudwatch")
logs = session.client("logs")


# ─── 알람 ─────────────────────────────────────────────────────────────────────

def list_alarms_by_state(state: str = "ALARM") -> list[dict]:
    """
    특정 상태의 알람 목록
    state: ALARM | OK | INSUFFICIENT_DATA
    """
    paginator = cw.get_paginator("describe_alarms")
    alarms = []

    for page in paginator.paginate(StateValue=state):
        for alarm in page["MetricAlarms"]:
            alarms.append({
                "name": alarm["AlarmName"],
                "state": alarm["StateValue"],
                "reason": alarm.get("StateReason", "-")[:80],
                "metric": alarm.get("MetricName", "-"),
                "namespace": alarm.get("Namespace", "-"),
                "updated": alarm["StateUpdatedTimestamp"].isoformat(),
            })

    return alarms


def get_alarm_history(alarm_name: str, days: int = 7) -> list[dict]:
    """알람 상태 변경 이력"""
    start_time = datetime.now(timezone.utc) - timedelta(days=days)

    paginator = cw.get_paginator("describe_alarm_history")
    history = []

    for page in paginator.paginate(
        AlarmName=alarm_name,
        StartDate=start_time,
        HistoryItemType="StateUpdate",
    ):
        for item in page["AlarmHistoryItems"]:
            history.append({
                "timestamp": item["Timestamp"].isoformat(),
                "summary": item["HistorySummary"],
            })

    return history


# ─── 메트릭 데이터 ────────────────────────────────────────────────────────────

def get_metric_statistics(
    namespace: str,
    metric_name: str,
    dimensions: list[dict],
    period: int = 300,
    hours: int = 1,
    statistics: list[str] = None,
) -> list[dict]:
    """
    메트릭 통계 데이터 조회
    dimensions 예시: [{"Name": "InstanceId", "Value": "i-xxxxxxxx"}]
    """
    if statistics is None:
        statistics = ["Average", "Maximum"]

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(hours=hours)

    resp = cw.get_metric_statistics(
        Namespace=namespace,
        MetricName=metric_name,
        Dimensions=dimensions,
        StartTime=start_time,
        EndTime=end_time,
        Period=period,
        Statistics=statistics,
    )

    datapoints = sorted(resp["Datapoints"], key=lambda x: x["Timestamp"])
    return [
        {
            "timestamp": dp["Timestamp"].strftime("%Y-%m-%d %H:%M"),
            **{stat: round(dp.get(stat, 0), 2) for stat in statistics},
        }
        for dp in datapoints
    ]


def get_ec2_cpu(instance_id: str, hours: int = 1) -> list[dict]:
    """EC2 CPU 사용률"""
    return get_metric_statistics(
        namespace="AWS/EC2",
        metric_name="CPUUtilization",
        dimensions=[{"Name": "InstanceId", "Value": instance_id}],
        hours=hours,
    )


def get_rds_metrics(db_identifier: str, hours: int = 1) -> dict:
    """RDS 주요 메트릭 (CPU, 커넥션, FreeStorage)"""
    dims = [{"Name": "DBInstanceIdentifier", "Value": db_identifier}]

    return {
        "cpu": get_metric_statistics("AWS/RDS", "CPUUtilization", dims, hours=hours),
        "connections": get_metric_statistics("AWS/RDS", "DatabaseConnections", dims, hours=hours),
        "free_storage_gb": get_metric_statistics(
            "AWS/RDS", "FreeStorageSpace", dims,
            statistics=["Minimum"], hours=hours
        ),
    }


def get_alb_metrics(lb_arn_suffix: str, hours: int = 1) -> dict:
    """ALB 요청 수 및 에러율"""
    dims = [{"Name": "LoadBalancer", "Value": lb_arn_suffix}]

    request_count = get_metric_statistics(
        "AWS/ApplicationELB", "RequestCount", dims,
        statistics=["Sum"], hours=hours
    )
    errors_5xx = get_metric_statistics(
        "AWS/ApplicationELB", "HTTPCode_ELB_5XX_Count", dims,
        statistics=["Sum"], hours=hours
    )
    target_errors = get_metric_statistics(
        "AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", dims,
        statistics=["Sum"], hours=hours
    )
    latency = get_metric_statistics(
        "AWS/ApplicationELB", "TargetResponseTime", dims,
        statistics=["Average", "Maximum"], hours=hours
    )

    return {
        "request_count": request_count,
        "elb_5xx": errors_5xx,
        "target_5xx": target_errors,
        "latency_ms": latency,
    }


def get_sqs_metrics(queue_name: str, hours: int = 1) -> dict:
    """SQS 메시지 수 및 지연 모니터링"""
    dims = [{"Name": "QueueName", "Value": queue_name}]

    return {
        "visible_messages": get_metric_statistics(
            "AWS/SQS", "ApproximateNumberOfMessagesVisible", dims, hours=hours,
            statistics=["Maximum"]
        ),
        "not_visible": get_metric_statistics(
            "AWS/SQS", "ApproximateNumberOfMessagesNotVisible", dims, hours=hours,
            statistics=["Maximum"]
        ),
        "age_of_oldest_message": get_metric_statistics(
            "AWS/SQS", "ApproximateAgeOfOldestMessage", dims, hours=hours,
            statistics=["Maximum"]
        ),
    }


# ─── 커스텀 메트릭 발행 ────────────────────────────────────────────────────────

def put_custom_metric(
    namespace: str,
    metric_name: str,
    value: float,
    unit: str = "Count",
    dimensions: Optional[list[dict]] = None,
) -> None:
    """
    커스텀 메트릭 발행
    unit: Count | Bytes | Seconds | Percent | None 등
    """
    metric_data = {
        "MetricName": metric_name,
        "Value": value,
        "Unit": unit,
        "Timestamp": datetime.now(timezone.utc),
    }
    if dimensions:
        metric_data["Dimensions"] = dimensions

    cw.put_metric_data(Namespace=namespace, MetricData=[metric_data])
    print(f"[발행 완료] {namespace}/{metric_name} = {value} {unit}")


def put_batch_metrics(namespace: str, metrics: list[dict]) -> None:
    """
    커스텀 메트릭 배치 발행 (최대 20개)
    metrics 예시:
    [
        {"name": "ActiveUsers", "value": 100, "unit": "Count"},
        {"name": "QueueDepth", "value": 42, "unit": "Count", "dims": [{"Name": "Service", "Value": "api"}]},
    ]
    """
    metric_data = []
    for m in metrics:
        entry = {
            "MetricName": m["name"],
            "Value": m["value"],
            "Unit": m.get("unit", "Count"),
            "Timestamp": datetime.now(timezone.utc),
        }
        if "dims" in m:
            entry["Dimensions"] = m["dims"]
        metric_data.append(entry)

    # CloudWatch API는 한 번에 최대 20개
    for i in range(0, len(metric_data), 20):
        cw.put_metric_data(Namespace=namespace, MetricData=metric_data[i:i+20])

    print(f"[배치 발행 완료] {len(metric_data)}개 메트릭 → {namespace}")


# ─── Logs Insights ────────────────────────────────────────────────────────────

def run_logs_insights(
    log_group: str,
    query: str,
    hours: int = 1,
    timeout: int = 60,
) -> list[dict]:
    """
    Logs Insights 쿼리 실행 및 결과 반환
    """
    end_time = int(time.time())
    start_time = end_time - hours * 3600

    resp = logs.start_query(
        logGroupName=log_group,
        startTime=start_time,
        endTime=end_time,
        queryString=query,
    )
    query_id = resp["queryId"]
    print(f"쿼리 실행 중... (ID: {query_id})")

    deadline = time.time() + timeout
    while time.time() < deadline:
        result = logs.get_query_results(queryId=query_id)
        status = result["status"]

        if status == "Complete":
            return [
                {field["field"]: field["value"] for field in row}
                for row in result["results"]
            ]
        elif status in ("Failed", "Cancelled"):
            raise RuntimeError(f"쿼리 실패: {status}")

        time.sleep(2)

    raise TimeoutError(f"쿼리 타임아웃 ({timeout}s)")


def analyze_error_frequency(log_group: str, hours: int = 1) -> list[dict]:
    """에러 로그 빈도 분석 (5분 단위)"""
    return run_logs_insights(
        log_group=log_group,
        query="""
        fields @timestamp, @message
        | filter @message like /ERROR|error|Exception|WARN/
        | stats count() as cnt by bin(5m)
        | sort cnt desc
        | limit 24
        """,
        hours=hours,
    )


def analyze_lambda_performance(function_name: str, hours: int = 24) -> list[dict]:
    """Lambda 함수 성능 분석 (콜드스타트, 메모리 사용량)"""
    return run_logs_insights(
        log_group=f"/aws/lambda/{function_name}",
        query="""
        filter @type = 'REPORT'
        | fields @timestamp, @duration, @billedDuration, @initDuration, @memorySize, @maxMemoryUsed
        | stats
            count() as invocations,
            avg(@duration) as avgDurationMs,
            max(@duration) as maxDurationMs,
            sum(ispresent(@initDuration)) as coldStarts,
            avg(@maxMemoryUsed) as avgMemoryMB
          by bin(1h)
        | sort @timestamp desc
        """,
        hours=hours,
    )


def get_top_log_contributors(log_group: str, hours: int = 1, limit: int = 10) -> list[dict]:
    """로그 볼륨 상위 기여자 (IP, 경로 등) 추출"""
    return run_logs_insights(
        log_group=log_group,
        query=f"""
        fields @message
        | stats count() as cnt by @logStream
        | sort cnt desc
        | limit {limit}
        """,
        hours=hours,
    )


# ─── 로그 그룹 관리 ───────────────────────────────────────────────────────────

def list_log_groups(name_prefix: str = "") -> list[dict]:
    """로그 그룹 목록 (크기 순 정렬)"""
    paginator = logs.get_paginator("describe_log_groups")
    groups = []

    kwargs = {}
    if name_prefix:
        kwargs["logGroupNamePrefix"] = name_prefix

    for page in paginator.paginate(**kwargs):
        for lg in page["logGroups"]:
            groups.append({
                "name": lg["logGroupName"],
                "stored_bytes": lg.get("storedBytes", 0),
                "retention_days": lg.get("retentionInDays", "무제한"),
                "created": datetime.fromtimestamp(
                    lg.get("creationTime", 0) / 1000, tz=timezone.utc
                ).strftime("%Y-%m-%d"),
            })

    return sorted(groups, key=lambda x: x["stored_bytes"], reverse=True)


def set_log_retention(log_group: str, days: int) -> None:
    """로그 보존 기간 설정 (비용 절감)"""
    logs.put_retention_policy(logGroupName=log_group, retentionInDays=days)
    print(f"[설정 완료] {log_group} → {days}일 보존")


def find_log_groups_without_retention() -> list[dict]:
    """보존 기간 미설정 로그 그룹 (비용 무제한 증가 위험)"""
    all_groups = list_log_groups()
    return [g for g in all_groups if g["retention_days"] == "무제한"]


# ─── CLI 실행 ─────────────────────────────────────────────────────────────────

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


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""

    if cmd == "alarms":
        state = sys.argv[2] if len(sys.argv) > 2 else "ALARM"
        print_table(list_alarms_by_state(state))
    elif cmd == "alarm-history":
        print_table(get_alarm_history(sys.argv[2]))
    elif cmd == "ec2-cpu":
        print_table(get_ec2_cpu(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 1))
    elif cmd == "rds":
        result = get_rds_metrics(sys.argv[2])
        for k, v in result.items():
            print(f"\n[{k}]")
            print_table(v)
    elif cmd == "alb":
        result = get_alb_metrics(sys.argv[2])
        for k, v in result.items():
            print(f"\n[{k}]")
            print_table(v)
    elif cmd == "sqs":
        result = get_sqs_metrics(sys.argv[2])
        for k, v in result.items():
            print(f"\n[{k}]")
            print_table(v)
    elif cmd == "error-freq":
        hours = int(sys.argv[3]) if len(sys.argv) > 3 else 1
        print_table(analyze_error_frequency(sys.argv[2], hours))
    elif cmd == "lambda-perf":
        hours = int(sys.argv[3]) if len(sys.argv) > 3 else 24
        print_table(analyze_lambda_performance(sys.argv[2], hours))
    elif cmd == "log-groups":
        print_table(list_log_groups(sys.argv[2] if len(sys.argv) > 2 else ""))
    elif cmd == "no-retention":
        print_table(find_log_groups_without_retention())
    elif cmd == "put-metric":
        # 예: python cloudwatch_queries.py put-metric MyApp/API ActiveUsers 100
        put_custom_metric(sys.argv[2], sys.argv[3], float(sys.argv[4]))
    else:
        print("사용법: python cloudwatch_queries.py <명령> [인수]\n")
        print("  alarms [STATE]         알람 목록 (기본: ALARM)")
        print("  alarm-history NAME     알람 이력")
        print("  ec2-cpu INSTANCE [H]   EC2 CPU 사용률")
        print("  rds DB_ID              RDS 메트릭")
        print("  alb LB_SUFFIX          ALB 요청/에러")
        print("  sqs QUEUE_NAME         SQS 메시지 현황")
        print("  error-freq LOG_GROUP [H]  에러 빈도")
        print("  lambda-perf FUNCTION [H]  Lambda 성능")
        print("  log-groups [PREFIX]    로그 그룹 목록")
        print("  no-retention           보존기간 미설정 그룹")
        print("  put-metric NS NAME VALUE  커스텀 메트릭 발행")
