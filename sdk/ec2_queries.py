"""
EC2 실무 boto3 쿼리 모음
사용법: python ec2_queries.py <명령> [인수]
"""

import boto3
import sys
from datetime import datetime, timezone, timedelta
from typing import Optional
import json

session = boto3.Session(region_name="ap-northeast-2")
ec2 = session.client("ec2")
autoscaling = session.client("autoscaling")


# ─── 인스턴스 조회 ─────────────────────────────────────────────────────────────

def get_instance_name(instance: dict) -> str:
    """태그에서 Name 값 추출"""
    for tag in instance.get("Tags", []):
        if tag["Key"] == "Name":
            return tag["Value"]
    return "-"


def list_running_instances() -> list[dict]:
    """실행 중인 인스턴스 목록 반환"""
    paginator = ec2.get_paginator("describe_instances")
    instances = []

    for page in paginator.paginate(
        Filters=[{"Name": "instance-state-name", "Values": ["running"]}]
    ):
        for reservation in page["Reservations"]:
            for inst in reservation["Instances"]:
                instances.append({
                    "instance_id": inst["InstanceId"],
                    "name": get_instance_name(inst),
                    "type": inst["InstanceType"],
                    "private_ip": inst.get("PrivateIpAddress", "-"),
                    "public_ip": inst.get("PublicIpAddress", "-"),
                    "state": inst["State"]["Name"],
                    "az": inst["Placement"]["AvailabilityZone"],
                    "launch_time": inst["LaunchTime"].isoformat(),
                })

    return instances


def find_instances_by_tag(key: str, value: str) -> list[dict]:
    """태그로 인스턴스 필터링"""
    paginator = ec2.get_paginator("describe_instances")
    instances = []

    for page in paginator.paginate(
        Filters=[
            {"Name": f"tag:{key}", "Values": [value]},
            {"Name": "instance-state-name", "Values": ["running", "stopped"]},
        ]
    ):
        for reservation in page["Reservations"]:
            for inst in reservation["Instances"]:
                instances.append({
                    "instance_id": inst["InstanceId"],
                    "name": get_instance_name(inst),
                    "private_ip": inst.get("PrivateIpAddress", "-"),
                    "state": inst["State"]["Name"],
                    "type": inst["InstanceType"],
                })

    return instances


def get_instance_detail(instance_id: str) -> dict:
    """특정 인스턴스 상세 정보"""
    resp = ec2.describe_instances(InstanceIds=[instance_id])
    inst = resp["Reservations"][0]["Instances"][0]

    return {
        "instance_id": inst["InstanceId"],
        "name": get_instance_name(inst),
        "type": inst["InstanceType"],
        "state": inst["State"]["Name"],
        "private_ip": inst.get("PrivateIpAddress", "-"),
        "public_ip": inst.get("PublicIpAddress", "-"),
        "vpc_id": inst.get("VpcId", "-"),
        "subnet_id": inst.get("SubnetId", "-"),
        "key_name": inst.get("KeyName", "-"),
        "iam_profile": inst.get("IamInstanceProfile", {}).get("Arn", "-"),
        "security_groups": [sg["GroupId"] for sg in inst.get("SecurityGroups", [])],
        "az": inst["Placement"]["AvailabilityZone"],
        "launch_time": inst["LaunchTime"].isoformat(),
        "tags": {t["Key"]: t["Value"] for t in inst.get("Tags", [])},
    }


# ─── 비용 최적화 / 정리 대상 ────────────────────────────────────────────────────

def find_stopped_instances() -> list[dict]:
    """중지된 인스턴스 목록 (EBS 비용 발생 중)"""
    paginator = ec2.get_paginator("describe_instances")
    instances = []

    for page in paginator.paginate(
        Filters=[{"Name": "instance-state-name", "Values": ["stopped"]}]
    ):
        for reservation in page["Reservations"]:
            for inst in reservation["Instances"]:
                instances.append({
                    "instance_id": inst["InstanceId"],
                    "name": get_instance_name(inst),
                    "type": inst["InstanceType"],
                    "state_reason": inst.get("StateTransitionReason", "-"),
                    "launch_time": inst["LaunchTime"].isoformat(),
                })

    return instances


def find_unattached_ebs() -> list[dict]:
    """미연결 EBS 볼륨 (비용 낭비)"""
    paginator = ec2.get_paginator("describe_volumes")
    volumes = []

    for page in paginator.paginate(
        Filters=[{"Name": "status", "Values": ["available"]}]
    ):
        for vol in page["Volumes"]:
            volumes.append({
                "volume_id": vol["VolumeId"],
                "size_gb": vol["Size"],
                "type": vol["VolumeType"],
                "az": vol["AvailabilityZone"],
                "create_time": vol["CreateTime"].isoformat(),
                "iops": vol.get("Iops", "-"),
                "tags": {t["Key"]: t["Value"] for t in vol.get("Tags", [])},
            })

    return volumes


def find_gp2_volumes() -> list[dict]:
    """gp2 볼륨 탐지 (gp3 전환 시 20% 비용 절감)"""
    paginator = ec2.get_paginator("describe_volumes")
    volumes = []

    for page in paginator.paginate(
        Filters=[{"Name": "volume-type", "Values": ["gp2"]}]
    ):
        for vol in page["Volumes"]:
            attached_to = "-"
            if vol.get("Attachments"):
                attached_to = vol["Attachments"][0]["InstanceId"]

            volumes.append({
                "volume_id": vol["VolumeId"],
                "size_gb": vol["Size"],
                "attached_to": attached_to,
                "state": vol["State"],
                "create_time": vol["CreateTime"].isoformat(),
            })

    return volumes


def find_unassociated_eip() -> list[dict]:
    """미연결 EIP (시간당 $0.005 과금)"""
    resp = ec2.describe_addresses()
    return [
        {
            "public_ip": addr["PublicIp"],
            "allocation_id": addr["AllocationId"],
            "domain": addr["Domain"],
        }
        for addr in resp["Addresses"]
        if "AssociationId" not in addr
    ]


# ─── 보안 감사 ────────────────────────────────────────────────────────────────

def find_open_security_groups(port: Optional[int] = None) -> list[dict]:
    """
    0.0.0.0/0 인바운드 허용된 보안 그룹 탐지
    port 지정 시 해당 포트만 필터 (None이면 전체)
    """
    paginator = ec2.get_paginator("describe_security_groups")
    results = []

    for page in paginator.paginate():
        for sg in page["SecurityGroups"]:
            for rule in sg.get("IpPermissions", []):
                for ip_range in rule.get("IpRanges", []):
                    if ip_range.get("CidrIp") != "0.0.0.0/0":
                        continue

                    from_port = rule.get("FromPort")
                    to_port = rule.get("ToPort")

                    if port is not None and from_port is not None:
                        if not (from_port <= port <= to_port):
                            continue

                    results.append({
                        "sg_id": sg["GroupId"],
                        "sg_name": sg["GroupName"],
                        "vpc_id": sg.get("VpcId", "-"),
                        "protocol": rule.get("IpProtocol", "-"),
                        "from_port": from_port,
                        "to_port": to_port,
                        "cidr": "0.0.0.0/0",
                    })

    return results


# ─── ASG ──────────────────────────────────────────────────────────────────────

def list_all_asg() -> list[dict]:
    """모든 ASG 현황 요약"""
    paginator = autoscaling.get_paginator("describe_auto_scaling_groups")
    groups = []

    for page in paginator.paginate():
        for asg in page["AutoScalingGroups"]:
            groups.append({
                "name": asg["AutoScalingGroupName"],
                "min": asg["MinSize"],
                "max": asg["MaxSize"],
                "desired": asg["DesiredCapacity"],
                "current": len(asg["Instances"]),
                "healthy": sum(1 for i in asg["Instances"] if i["HealthStatus"] == "Healthy"),
            })

    return groups


def get_asg_instances(asg_name: str) -> list[dict]:
    """특정 ASG 인스턴스 목록"""
    resp = autoscaling.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
    if not resp["AutoScalingGroups"]:
        return []

    return [
        {
            "instance_id": inst["InstanceId"],
            "lifecycle_state": inst["LifecycleState"],
            "health_status": inst["HealthStatus"],
            "az": inst["AvailabilityZone"],
            "type": inst["InstanceType"],
        }
        for inst in resp["AutoScalingGroups"][0]["Instances"]
    ]


# ─── 인스턴스 제어 ─────────────────────────────────────────────────────────────

def start_instances(instance_ids: list[str]) -> dict:
    """인스턴스 시작"""
    resp = ec2.start_instances(InstanceIds=instance_ids)
    return {
        inst["InstanceId"]: {
            "previous_state": inst["PreviousState"]["Name"],
            "current_state": inst["CurrentState"]["Name"],
        }
        for inst in resp["StartingInstances"]
    }


def stop_instances(instance_ids: list[str], force: bool = False) -> dict:
    """인스턴스 중지"""
    resp = ec2.stop_instances(InstanceIds=instance_ids, Force=force)
    return {
        inst["InstanceId"]: {
            "previous_state": inst["PreviousState"]["Name"],
            "current_state": inst["CurrentState"]["Name"],
        }
        for inst in resp["StoppingInstances"]
    }


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


COMMANDS = {
    "running": (list_running_instances, "실행 중인 인스턴스"),
    "stopped": (find_stopped_instances, "중지된 인스턴스"),
    "unattached-ebs": (find_unattached_ebs, "미연결 EBS"),
    "gp2": (find_gp2_volumes, "gp2 볼륨 목록"),
    "eip": (find_unassociated_eip, "미연결 EIP"),
    "open-sg": (find_open_security_groups, "0.0.0.0/0 허용 SG"),
    "all-asg": (list_all_asg, "모든 ASG 현황"),
}

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""

    if cmd == "tag" and len(sys.argv) >= 4:
        print_table(find_instances_by_tag(sys.argv[2], sys.argv[3]))
    elif cmd == "detail" and len(sys.argv) >= 3:
        print(json.dumps(get_instance_detail(sys.argv[2]), indent=2, ensure_ascii=False))
    elif cmd == "asg" and len(sys.argv) >= 3:
        print_table(get_asg_instances(sys.argv[2]))
    elif cmd in COMMANDS:
        print_table(COMMANDS[cmd][0]())
    else:
        print("사용법: python ec2_queries.py <명령> [인수]\n")
        for k, (_, desc) in COMMANDS.items():
            print(f"  {k:<20} {desc}")
        print("  tag KEY VALUE        태그로 인스턴스 필터")
        print("  detail INSTANCE_ID   인스턴스 상세 정보")
        print("  asg ASG_NAME         ASG 인스턴스 목록")
