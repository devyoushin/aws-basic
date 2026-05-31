"""
EBS 스냅샷 자동 정리
보존 기간이 지난 EBS 스냅샷을 자동으로 삭제합니다.
AMI에 연결된 스냅샷은 안전하게 보호합니다.

트리거: EventBridge Scheduler
  cron(0 2 * * ? *)  → 매일 새벽 2시

필요 IAM 권한:
  - ec2:DescribeSnapshots
  - ec2:DescribeImages
  - ec2:DeleteSnapshot
  - ec2:CreateTags

환경 변수:
  - RETENTION_DAYS: 보존 기간 (기본: 30일)
  - DRY_RUN: "true" 이면 실제 삭제 안 함 (기본: false)
  - EXCLUDE_TAG_KEY: 이 태그가 있으면 삭제 제외 (기본: Permanent)
  - SNS_TOPIC_ARN: 결과 알림 SNS 토픽 ARN (선택)
  - MAX_DELETE_COUNT: 한 번에 삭제할 최대 개수 (기본: 100, 안전장치)
"""

import boto3
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ec2 = boto3.client("ec2")
sns_client = boto3.client("sns")

RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "30"))
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
EXCLUDE_TAG_KEY = os.environ.get("EXCLUDE_TAG_KEY", "Permanent")
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN", "")
MAX_DELETE_COUNT = int(os.environ.get("MAX_DELETE_COUNT", "100"))


def get_ami_snapshot_ids() -> set[str]:
    """
    AMI에 연결된 스냅샷 ID 수집
    이 스냅샷들은 AMI 등록 해제 전까지 삭제 불가 → 반드시 보호
    """
    paginator = ec2.get_paginator("describe_images")
    snapshot_ids = set()

    for page in paginator.paginate(Owners=["self"]):
        for image in page["Images"]:
            for mapping in image.get("BlockDeviceMappings", []):
                ebs = mapping.get("Ebs", {})
                if "SnapshotId" in ebs:
                    snapshot_ids.add(ebs["SnapshotId"])

    logger.info("AMI 연결 스냅샷 %d개 보호 대상", len(snapshot_ids))
    return snapshot_ids


def get_old_snapshots(cutoff: datetime, protected_ids: set[str]) -> list[dict]:
    """보존 기간이 지난 삭제 후보 스냅샷 수집"""
    account_id = boto3.client("sts").get_caller_identity()["Account"]
    paginator = ec2.get_paginator("describe_snapshots")
    candidates = []

    for page in paginator.paginate(OwnerIds=[account_id]):
        for snap in page["Snapshots"]:
            # AMI 연결 스냅샷 제외
            if snap["SnapshotId"] in protected_ids:
                continue

            # 보존 태그가 있으면 제외
            tags = {t["Key"]: t["Value"] for t in snap.get("Tags", [])}
            if EXCLUDE_TAG_KEY in tags:
                continue

            # 보존 기간 확인
            start_time = snap["StartTime"].replace(tzinfo=timezone.utc)
            if start_time >= cutoff:
                continue

            age_days = (datetime.now(timezone.utc) - start_time).days
            candidates.append({
                "snapshot_id": snap["SnapshotId"],
                "volume_id": snap.get("VolumeId", "-"),
                "size_gb": snap["VolumeSize"],
                "start_time": start_time.isoformat(),
                "age_days": age_days,
                "description": snap.get("Description", "")[:80],
                "tags": tags,
            })

    # 오래된 순 정렬
    return sorted(candidates, key=lambda x: x["start_time"])


def delete_snapshot(snapshot_id: str) -> bool:
    """스냅샷 삭제"""
    try:
        ec2.delete_snapshot(SnapshotId=snapshot_id)
        logger.info("삭제 완료: %s", snapshot_id)
        return True
    except ec2.exceptions.ClientError as e:
        error_code = e.response["Error"]["Code"]
        if error_code == "InvalidSnapshot.InUse":
            logger.warning("사용 중인 스냅샷 건너뜀: %s", snapshot_id)
        else:
            logger.error("삭제 실패 %s: %s", snapshot_id, e)
        return False


def send_report(report: dict) -> None:
    """SNS로 정리 결과 발송"""
    if not SNS_TOPIC_ARN:
        return

    message = (
        f"[EBS 스냅샷 정리 결과]\n"
        f"- 보존 기간: {RETENTION_DAYS}일\n"
        f"- 검사 대상: {report['total_candidates']}개\n"
        f"- {'시뮬레이션' if DRY_RUN else '삭제 완료'}: {report['deleted_count']}개\n"
        f"- 절약 스토리지: {report['freed_gb']:.1f} GB\n"
        f"- 실패: {report['failed_count']}개\n"
    )

    if report.get("deleted_snapshots"):
        message += f"\n삭제된 스냅샷:\n"
        for snap in report["deleted_snapshots"][:10]:
            message += f"  {snap['snapshot_id']} ({snap['age_days']}일, {snap['size_gb']}GB)\n"

    sns_client.publish(
        TopicArn=SNS_TOPIC_ARN,
        Subject=f"[EBS 정리] {'DRY RUN: ' if DRY_RUN else ''}{report['deleted_count']}개 삭제",
        Message=message,
    )
    logger.info("SNS 리포트 전송 완료")


def lambda_handler(event: dict, context) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    logger.info("보존 기간: %d일 (기준일: %s), DRY_RUN: %s", RETENTION_DAYS, cutoff.date(), DRY_RUN)

    # AMI에 연결된 스냅샷 ID 수집
    protected_ids = get_ami_snapshot_ids()

    # 삭제 후보 수집
    candidates = get_old_snapshots(cutoff, protected_ids)
    logger.info("삭제 후보: %d개", len(candidates))

    # 안전장치: 최대 삭제 개수 제한
    if len(candidates) > MAX_DELETE_COUNT:
        logger.warning("삭제 후보(%d개) > MAX_DELETE_COUNT(%d). 최대치로 제한", len(candidates), MAX_DELETE_COUNT)
        candidates = candidates[:MAX_DELETE_COUNT]

    deleted = []
    failed = []
    freed_gb = 0

    for snap in candidates:
        if DRY_RUN:
            logger.info("[DRY RUN] 삭제 예정: %s (%d일, %dGB)",
                       snap["snapshot_id"], snap["age_days"], snap["size_gb"])
            deleted.append(snap)
            freed_gb += snap["size_gb"]
        else:
            success = delete_snapshot(snap["snapshot_id"])
            if success:
                deleted.append(snap)
                freed_gb += snap["size_gb"]
            else:
                failed.append(snap["snapshot_id"])

    report = {
        "dry_run": DRY_RUN,
        "retention_days": RETENTION_DAYS,
        "total_candidates": len(candidates),
        "deleted_count": len(deleted),
        "failed_count": len(failed),
        "freed_gb": freed_gb,
        "deleted_snapshots": deleted,
        "failed_snapshots": failed,
    }

    logger.info("완료: %s", json.dumps(report, ensure_ascii=False, default=str))
    send_report(report)

    return {"statusCode": 200, **report}
