"""
EC2 자동 시작/중지 스케줄러
태그 기반으로 EC2 인스턴스를 자동으로 시작/중지합니다.

트리거: EventBridge Scheduler (Cron)
  - 중지: cron(0 21 ? * MON-FRI *)  → 평일 오후 9시 (KST 기준 UTC+9이면 cron(0 12 ...))
  - 시작: cron(0 0 ? * MON-FRI *)   → 평일 자정 (KST 오전 9시)

필요 IAM 권한:
  - ec2:DescribeInstances
  - ec2:StartInstances
  - ec2:StopInstances

환경 변수:
  - ACTION: "start" | "stop"
  - TAG_KEY: 대상 태그 키 (기본: AutoSchedule)
  - TAG_VALUE: 대상 태그 값 (기본: true)
  - DRY_RUN: "true" 이면 실제 실행 안 함 (기본: false)

태그 설정 예시:
  aws ec2 create-tags --resources i-xxxxxx --tags Key=AutoSchedule,Value=true
"""

import boto3
import os
import json
import logging
from typing import Optional

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ec2 = boto3.client("ec2")

ACTION = os.environ.get("ACTION", "stop")           # "start" | "stop"
TAG_KEY = os.environ.get("TAG_KEY", "AutoSchedule")
TAG_VALUE = os.environ.get("TAG_VALUE", "true")
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"


def get_target_instances() -> list[dict]:
    """대상 태그가 붙은 인스턴스 조회"""
    target_state = "running" if ACTION == "stop" else "stopped"

    paginator = ec2.get_paginator("describe_instances")
    instances = []

    for page in paginator.paginate(
        Filters=[
            {"Name": f"tag:{TAG_KEY}", "Values": [TAG_VALUE]},
            {"Name": "instance-state-name", "Values": [target_state]},
        ]
    ):
        for reservation in page["Reservations"]:
            for inst in reservation["Instances"]:
                name = next(
                    (t["Value"] for t in inst.get("Tags", []) if t["Key"] == "Name"),
                    inst["InstanceId"],
                )
                instances.append({
                    "instance_id": inst["InstanceId"],
                    "name": name,
                    "state": inst["State"]["Name"],
                    "type": inst["InstanceType"],
                    "az": inst["Placement"]["AvailabilityZone"],
                })

    return instances


def perform_action(instance_ids: list[str]) -> dict:
    """인스턴스 시작 또는 중지"""
    if not instance_ids:
        return {}

    if DRY_RUN:
        logger.info("[DRY RUN] 실제 실행 없음. 대상: %s", instance_ids)
        return {"dry_run": True, "targets": instance_ids}

    if ACTION == "stop":
        resp = ec2.stop_instances(InstanceIds=instance_ids)
        return {
            inst["InstanceId"]: inst["CurrentState"]["Name"]
            for inst in resp["StoppingInstances"]
        }
    else:
        resp = ec2.start_instances(InstanceIds=instance_ids)
        return {
            inst["InstanceId"]: inst["CurrentState"]["Name"]
            for inst in resp["StartingInstances"]
        }


def lambda_handler(event: dict, context) -> dict:
    logger.info("ACTION=%s, TAG=%s:%s, DRY_RUN=%s", ACTION, TAG_KEY, TAG_VALUE, DRY_RUN)

    instances = get_target_instances()

    if not instances:
        logger.info("대상 인스턴스 없음")
        return {"statusCode": 200, "message": "대상 인스턴스 없음", "action": ACTION}

    logger.info("대상 인스턴스 %d개: %s", len(instances), [i["instance_id"] for i in instances])

    result = perform_action([i["instance_id"] for i in instances])

    logger.info("완료: %s", json.dumps(result))

    return {
        "statusCode": 200,
        "action": ACTION,
        "target_count": len(instances),
        "instances": instances,
        "result": result,
    }
