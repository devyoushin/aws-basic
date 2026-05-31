"""
RDS / Aurora 스냅샷 자동 관리
수동 스냅샷 생성, 크로스 리전 복사, 오래된 스냅샷 정리를 자동화합니다.

트리거: EventBridge Scheduler
  스냅샷 생성: cron(0 18 * * ? *)  → 매일 오후 6시
  정리:        cron(0 19 * * ? *)  → 매일 오후 7시 (생성 후)

필요 IAM 권한:
  - rds:CreateDBSnapshot
  - rds:CreateDBClusterSnapshot (Aurora)
  - rds:DescribeDBSnapshots
  - rds:DescribeDBClusterSnapshots
  - rds:CopyDBSnapshot
  - rds:CopyDBClusterSnapshot
  - rds:DeleteDBSnapshot
  - rds:DeleteDBClusterSnapshot
  - rds:ListTagsForResource
  - rds:AddTagsToResource
  - kms:CreateGrant, kms:DescribeKey (암호화 스냅샷 복사 시)

환경 변수:
  - ACTION: "create" | "copy" | "cleanup" | "all"
  - DB_IDENTIFIERS: 쉼표 구분 DB 식별자 목록 (인스턴스 또는 클러스터)
  - IS_CLUSTER: "true" 이면 Aurora 클러스터 스냅샷 (기본: false)
  - RETENTION_DAYS: 스냅샷 보존 기간 (기본: 7)
  - COPY_REGION: 크로스 리전 복사 대상 리전 (선택, 예: us-east-1)
  - COPY_KMS_KEY_ID: 복사 대상 KMS 키 ID (암호화 시)
  - SNS_TOPIC_ARN: 결과 알림 SNS 토픽 ARN (선택)
  - DRY_RUN: "true" 이면 실제 실행 안 함
"""

import boto3
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SOURCE_REGION = os.environ.get("AWS_REGION", "ap-northeast-2")
rds = boto3.client("rds", region_name=SOURCE_REGION)
sns_client = boto3.client("sns")

ACTION = os.environ.get("ACTION", "all")
DB_IDENTIFIERS = [x.strip() for x in os.environ.get("DB_IDENTIFIERS", "").split(",") if x.strip()]
IS_CLUSTER = os.environ.get("IS_CLUSTER", "false").lower() == "true"
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "7"))
COPY_REGION = os.environ.get("COPY_REGION", "")
COPY_KMS_KEY_ID = os.environ.get("COPY_KMS_KEY_ID", "")
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN", "")
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"

# 스냅샷 식별 태그
MANAGED_TAG = {"Key": "ManagedBy", "Value": "AutoSnapshotLambda"}


def make_snapshot_id(db_id: str) -> str:
    """스냅샷 식별자 생성 (날짜 포함)"""
    today = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    # RDS 식별자는 하이픈만 허용
    safe_id = db_id.replace("_", "-").lower()
    return f"auto-{safe_id}-{today}"


# ─── 스냅샷 생성 ──────────────────────────────────────────────────────────────

def create_instance_snapshot(db_id: str) -> Optional[dict]:
    """RDS 인스턴스 수동 스냅샷 생성"""
    snapshot_id = make_snapshot_id(db_id)

    if DRY_RUN:
        logger.info("[DRY RUN] 스냅샷 생성 예정: %s → %s", db_id, snapshot_id)
        return {"snapshot_id": snapshot_id, "db_id": db_id, "dry_run": True}

    try:
        resp = rds.create_db_snapshot(
            DBInstanceIdentifier=db_id,
            DBSnapshotIdentifier=snapshot_id,
            Tags=[MANAGED_TAG, {"Key": "SourceDB", "Value": db_id}],
        )
        snap = resp["DBSnapshot"]
        logger.info("스냅샷 생성 요청: %s (상태: %s)", snapshot_id, snap["Status"])
        return {
            "snapshot_id": snap["DBSnapshotIdentifier"],
            "db_id": db_id,
            "status": snap["Status"],
            "type": "instance",
        }
    except rds.exceptions.DBInstanceNotFoundFault:
        logger.error("DB 인스턴스 없음: %s", db_id)
        return None
    except rds.exceptions.DBSnapshotAlreadyExistsFault:
        logger.warning("스냅샷 이미 존재: %s", snapshot_id)
        return None


def create_cluster_snapshot(cluster_id: str) -> Optional[dict]:
    """Aurora 클러스터 스냅샷 생성"""
    snapshot_id = make_snapshot_id(cluster_id)

    if DRY_RUN:
        logger.info("[DRY RUN] 클러스터 스냅샷 생성 예정: %s → %s", cluster_id, snapshot_id)
        return {"snapshot_id": snapshot_id, "cluster_id": cluster_id, "dry_run": True}

    try:
        resp = rds.create_db_cluster_snapshot(
            DBClusterIdentifier=cluster_id,
            DBClusterSnapshotIdentifier=snapshot_id,
            Tags=[MANAGED_TAG, {"Key": "SourceCluster", "Value": cluster_id}],
        )
        snap = resp["DBClusterSnapshot"]
        logger.info("클러스터 스냅샷 생성 요청: %s (상태: %s)", snapshot_id, snap["Status"])
        return {
            "snapshot_id": snap["DBClusterSnapshotIdentifier"],
            "cluster_id": cluster_id,
            "status": snap["Status"],
            "type": "cluster",
        }
    except Exception as e:
        logger.error("클러스터 스냅샷 생성 실패 %s: %s", cluster_id, e)
        return None


# ─── 크로스 리전 복사 ────────────────────────────────────────────────────────

def copy_snapshot_to_region(snapshot_id: str, source_region: str, dest_region: str) -> Optional[dict]:
    """스냅샷을 다른 리전으로 복사 (DR 목적)"""
    if not dest_region:
        return None

    dest_rds = boto3.client("rds", region_name=dest_region)
    source_arn = (
        f"arn:aws:rds:{source_region}:"
        f"{boto3.client('sts').get_caller_identity()['Account']}:"
        f"snapshot:{snapshot_id}"
    )

    copy_id = f"copy-{snapshot_id}"

    if DRY_RUN:
        logger.info("[DRY RUN] 리전 복사 예정: %s → %s", source_region, dest_region)
        return {"copy_id": copy_id, "dest_region": dest_region, "dry_run": True}

    try:
        kwargs = {
            "SourceDBSnapshotIdentifier": source_arn,
            "TargetDBSnapshotIdentifier": copy_id,
            "SourceRegion": source_region,
            "Tags": [MANAGED_TAG],
            "CopyTags": True,
        }
        if COPY_KMS_KEY_ID:
            kwargs["KmsKeyId"] = COPY_KMS_KEY_ID

        resp = dest_rds.copy_db_snapshot(**kwargs)
        snap = resp["DBSnapshot"]
        logger.info("리전 복사 시작: %s → %s/%s", snapshot_id, dest_region, copy_id)
        return {
            "copy_id": snap["DBSnapshotIdentifier"],
            "dest_region": dest_region,
            "status": snap["Status"],
        }
    except Exception as e:
        logger.error("리전 복사 실패: %s", e)
        return None


# ─── 스냅샷 정리 ──────────────────────────────────────────────────────────────

def cleanup_old_snapshots() -> dict:
    """Lambda가 생성한 오래된 스냅샷 정리"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    deleted_count = 0
    freed_gb = 0
    errors = []

    if IS_CLUSTER:
        paginator = rds.get_paginator("describe_db_cluster_snapshots")
        for page in paginator.paginate(SnapshotType="manual"):
            for snap in page["DBClusterSnapshots"]:
                snap_id = snap["DBClusterSnapshotIdentifier"]
                if not snap_id.startswith("auto-"):
                    continue

                create_time = snap["SnapshotCreateTime"].replace(tzinfo=timezone.utc)
                if create_time >= cutoff:
                    continue

                age_days = (datetime.now(timezone.utc) - create_time).days
                logger.info("클러스터 스냅샷 삭제 대상: %s (%d일)", snap_id, age_days)

                if not DRY_RUN:
                    try:
                        rds.delete_db_cluster_snapshot(DBClusterSnapshotIdentifier=snap_id)
                        deleted_count += 1
                    except Exception as e:
                        errors.append(f"{snap_id}: {e}")
                else:
                    deleted_count += 1
    else:
        # 특정 DB의 스냅샷만 정리
        for db_id in DB_IDENTIFIERS:
            paginator = rds.get_paginator("describe_db_snapshots")
            for page in paginator.paginate(DBInstanceIdentifier=db_id, SnapshotType="manual"):
                for snap in page["DBSnapshots"]:
                    snap_id = snap["DBSnapshotIdentifier"]
                    if not snap_id.startswith("auto-"):
                        continue

                    create_time = snap["SnapshotCreateTime"].replace(tzinfo=timezone.utc)
                    if create_time >= cutoff:
                        continue

                    age_days = (datetime.now(timezone.utc) - create_time).days
                    size_gb = snap.get("AllocatedStorage", 0)
                    logger.info("스냅샷 삭제 대상: %s (%d일, %dGB)", snap_id, age_days, size_gb)

                    if not DRY_RUN:
                        try:
                            rds.delete_db_snapshot(DBSnapshotIdentifier=snap_id)
                            deleted_count += 1
                            freed_gb += size_gb
                        except Exception as e:
                            errors.append(f"{snap_id}: {e}")
                    else:
                        deleted_count += 1
                        freed_gb += size_gb

    return {
        "deleted_count": deleted_count,
        "freed_gb": freed_gb,
        "errors": errors,
        "dry_run": DRY_RUN,
    }


# ─── 핸들러 ───────────────────────────────────────────────────────────────────

def lambda_handler(event: dict, context) -> dict:
    logger.info("ACTION=%s, DB_IDENTIFIERS=%s, IS_CLUSTER=%s, DRY_RUN=%s",
                ACTION, DB_IDENTIFIERS, IS_CLUSTER, DRY_RUN)

    results = {
        "action": ACTION,
        "created": [],
        "copied": [],
        "cleanup": None,
    }

    # 스냅샷 생성
    if ACTION in ("create", "all"):
        for db_id in DB_IDENTIFIERS:
            if IS_CLUSTER:
                result = create_cluster_snapshot(db_id)
            else:
                result = create_instance_snapshot(db_id)

            if result:
                results["created"].append(result)

                # 크로스 리전 복사
                if COPY_REGION and not DRY_RUN:
                    copy_result = copy_snapshot_to_region(
                        result["snapshot_id"], SOURCE_REGION, COPY_REGION
                    )
                    if copy_result:
                        results["copied"].append(copy_result)

    # 정리
    if ACTION in ("cleanup", "all"):
        results["cleanup"] = cleanup_old_snapshots()

    logger.info("완료: %s", json.dumps(results, ensure_ascii=False, default=str))

    # SNS 알림
    if SNS_TOPIC_ARN:
        created_count = len(results["created"])
        cleanup = results.get("cleanup") or {}
        subject = (
            f"[RDS 스냅샷] 생성 {created_count}개, "
            f"삭제 {cleanup.get('deleted_count', 0)}개"
        )
        sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject,
            Message=json.dumps(results, ensure_ascii=False, default=str),
        )

    return {"statusCode": 200, **results}
