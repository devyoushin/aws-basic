"""
VPC 실무 boto3 쿼리 모음
사용법: python vpc_queries.py <명령> [인수]
"""

import boto3
import sys
import json

session = boto3.Session(region_name="ap-northeast-2")
ec2 = session.client("ec2")


# ─── VPC 조회 ─────────────────────────────────────────────────────────────────

def get_tag_name(tags: list) -> str:
    for t in tags or []:
        if t["Key"] == "Name":
            return t["Value"]
    return "-"


def list_vpcs() -> list[dict]:
    """모든 VPC 목록"""
    resp = ec2.describe_vpcs()
    return [
        {
            "vpc_id": v["VpcId"],
            "cidr": v["CidrBlock"],
            "name": get_tag_name(v.get("Tags", [])),
            "is_default": v["IsDefault"],
            "state": v["State"],
        }
        for v in resp["Vpcs"]
    ]


def describe_vpc(vpc_id: str) -> dict:
    """특정 VPC 상세 (Secondary CIDR 포함)"""
    resp = ec2.describe_vpcs(VpcIds=[vpc_id])
    v = resp["Vpcs"][0]
    return {
        "vpc_id": v["VpcId"],
        "name": get_tag_name(v.get("Tags", [])),
        "cidr_primary": v["CidrBlock"],
        "cidr_associations": [
            a["CidrBlock"] for a in v.get("CidrBlockAssociationSet", [])
        ],
        "is_default": v["IsDefault"],
        "state": v["State"],
        "dhcp_options_id": v.get("DhcpOptionsId", "-"),
    }


# ─── 서브넷 ───────────────────────────────────────────────────────────────────

def list_subnets(vpc_id: str) -> list[dict]:
    """특정 VPC의 서브넷 목록"""
    paginator = ec2.get_paginator("describe_subnets")
    subnets = []

    for page in paginator.paginate(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]):
        for s in page["Subnets"]:
            subnets.append({
                "subnet_id": s["SubnetId"],
                "cidr": s["CidrBlock"],
                "az": s["AvailabilityZone"],
                "name": get_tag_name(s.get("Tags", [])),
                "available_ips": s["AvailableIpAddressCount"],
                "auto_public_ip": s["MapPublicIpOnLaunch"],
            })

    return sorted(subnets, key=lambda x: x["az"])


def find_low_ip_subnets(threshold: int = 20) -> list[dict]:
    """가용 IP가 임계값 미만인 서브넷 탐지 (IP 고갈 위험)"""
    paginator = ec2.get_paginator("describe_subnets")
    results = []

    for page in paginator.paginate():
        for s in page["Subnets"]:
            if s["AvailableIpAddressCount"] < threshold:
                results.append({
                    "subnet_id": s["SubnetId"],
                    "cidr": s["CidrBlock"],
                    "az": s["AvailabilityZone"],
                    "name": get_tag_name(s.get("Tags", [])),
                    "available_ips": s["AvailableIpAddressCount"],
                    "vpc_id": s["VpcId"],
                })

    return sorted(results, key=lambda x: x["available_ips"])


# ─── 라우팅 테이블 ────────────────────────────────────────────────────────────

def list_route_tables(vpc_id: str) -> list[dict]:
    """특정 VPC의 라우팅 테이블 목록"""
    resp = ec2.describe_route_tables(
        Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
    )
    return [
        {
            "rtb_id": rtb["RouteTableId"],
            "name": get_tag_name(rtb.get("Tags", [])),
            "associated_subnets": [
                a["SubnetId"]
                for a in rtb.get("Associations", [])
                if "SubnetId" in a
            ],
            "is_main": any(
                a.get("Main", False) for a in rtb.get("Associations", [])
            ),
            "route_count": len(rtb.get("Routes", [])),
        }
        for rtb in resp["RouteTables"]
    ]


def describe_routes(rtb_id: str) -> list[dict]:
    """특정 라우팅 테이블의 경로 상세"""
    resp = ec2.describe_route_tables(RouteTableIds=[rtb_id])
    routes = []

    for route in resp["RouteTables"][0].get("Routes", []):
        routes.append({
            "destination": route.get("DestinationCidrBlock", route.get("DestinationPrefixListId", "-")),
            "target": (
                route.get("GatewayId")
                or route.get("NatGatewayId")
                or route.get("TransitGatewayId")
                or route.get("VpcPeeringConnectionId")
                or route.get("NetworkInterfaceId")
                or "-"
            ),
            "state": route.get("State", "-"),
            "origin": route.get("Origin", "-"),
        })

    return routes


# ─── 게이트웨이 ───────────────────────────────────────────────────────────────

def list_nat_gateways() -> list[dict]:
    """NAT 게이트웨이 목록 및 상태"""
    paginator = ec2.get_paginator("describe_nat_gateways")
    gateways = []

    for page in paginator.paginate():
        for ngw in page["NatGateways"]:
            public_ip = "-"
            if ngw.get("NatGatewayAddresses"):
                public_ip = ngw["NatGatewayAddresses"][0].get("PublicIp", "-")

            gateways.append({
                "nat_gw_id": ngw["NatGatewayId"],
                "name": get_tag_name(ngw.get("Tags", [])),
                "subnet_id": ngw.get("SubnetId", "-"),
                "vpc_id": ngw.get("VpcId", "-"),
                "state": ngw["State"],
                "public_ip": public_ip,
                "create_time": ngw["CreateTime"].isoformat(),
            })

    return gateways


def list_vpc_endpoints(vpc_id: str = None) -> list[dict]:
    """VPC 엔드포인트 목록"""
    filters = []
    if vpc_id:
        filters.append({"Name": "vpc-id", "Values": [vpc_id]})

    paginator = ec2.get_paginator("describe_vpc_endpoints")
    endpoints = []

    for page in paginator.paginate(Filters=filters):
        for ep in page["VpcEndpoints"]:
            endpoints.append({
                "endpoint_id": ep["VpcEndpointId"],
                "service": ep["ServiceName"].split(".")[-1],  # 서비스명만 추출
                "full_service": ep["ServiceName"],
                "type": ep["VpcEndpointType"],
                "state": ep["State"],
                "vpc_id": ep["VpcId"],
            })

    return endpoints


# ─── 보안 그룹 ────────────────────────────────────────────────────────────────

def find_unused_security_groups() -> list[dict]:
    """ENI에 연결되지 않은 미사용 보안 그룹 탐지"""
    # 사용 중인 SG ID 수집
    paginator = ec2.get_paginator("describe_network_interfaces")
    used_sg_ids: set[str] = set()

    for page in paginator.paginate():
        for eni in page["NetworkInterfaces"]:
            for sg in eni.get("Groups", []):
                used_sg_ids.add(sg["GroupId"])

    # 전체 SG에서 미사용 탐지
    paginator2 = ec2.get_paginator("describe_security_groups")
    unused = []

    for page in paginator2.paginate():
        for sg in page["SecurityGroups"]:
            if sg["GroupName"] == "default":
                continue
            if sg["GroupId"] not in used_sg_ids:
                unused.append({
                    "sg_id": sg["GroupId"],
                    "name": sg["GroupName"],
                    "vpc_id": sg.get("VpcId", "-"),
                    "description": sg.get("Description", "-"),
                })

    return unused


def describe_sg_rules(sg_id: str) -> dict:
    """보안 그룹 인바운드/아웃바운드 규칙 상세"""
    resp = ec2.describe_security_groups(GroupIds=[sg_id])
    sg = resp["SecurityGroups"][0]

    def parse_rules(rules):
        result = []
        for rule in rules:
            cidrs = [r["CidrIp"] for r in rule.get("IpRanges", [])]
            cidrs += [r["CidrIpv6"] for r in rule.get("Ipv6Ranges", [])]
            sg_refs = [r["GroupId"] for r in rule.get("UserIdGroupPairs", [])]
            result.append({
                "protocol": rule.get("IpProtocol", "-"),
                "from_port": rule.get("FromPort", "ALL"),
                "to_port": rule.get("ToPort", "ALL"),
                "sources": cidrs + sg_refs,
            })
        return result

    return {
        "sg_id": sg["GroupId"],
        "name": sg["GroupName"],
        "vpc_id": sg.get("VpcId", "-"),
        "inbound": parse_rules(sg.get("IpPermissions", [])),
        "outbound": parse_rules(sg.get("IpPermissionsEgress", [])),
    }


# ─── Flow Logs ────────────────────────────────────────────────────────────────

def check_flow_logs(vpc_id: str = None) -> list[dict]:
    """VPC Flow Logs 활성화 여부 확인"""
    filters = []
    if vpc_id:
        filters.append({"Name": "resource-id", "Values": [vpc_id]})

    resp = ec2.describe_flow_logs(Filter=filters)
    return [
        {
            "flow_log_id": fl["FlowLogId"],
            "resource_id": fl["ResourceId"],
            "traffic_type": fl["TrafficType"],
            "status": fl["DeliverLogsStatus"],
            "destination_type": fl.get("LogDestinationType", "-"),
            "destination": fl.get("LogDestination", "-"),
        }
        for fl in resp["FlowLogs"]
    ]


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
    "vpcs":      (list_vpcs,           "전체 VPC 목록"),
    "nat":       (list_nat_gateways,   "NAT 게이트웨이 목록"),
    "unused-sg": (find_unused_security_groups, "미사용 보안 그룹 탐지"),
    "low-ip":    (lambda: find_low_ip_subnets(20), "IP 고갈 위험 서브넷"),
}

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""

    if cmd == "vpc" and len(sys.argv) >= 3:
        print(json.dumps(describe_vpc(sys.argv[2]), indent=2, ensure_ascii=False))
    elif cmd == "subnets" and len(sys.argv) >= 3:
        print_table(list_subnets(sys.argv[2]))
    elif cmd == "low-ip":
        threshold = int(sys.argv[2]) if len(sys.argv) >= 3 else 20
        print_table(find_low_ip_subnets(threshold))
    elif cmd == "rtb" and len(sys.argv) >= 3:
        print_table(list_route_tables(sys.argv[2]))
    elif cmd == "routes" and len(sys.argv) >= 3:
        print_table(describe_routes(sys.argv[2]))
    elif cmd == "endpoints":
        vpc_id = sys.argv[2] if len(sys.argv) >= 3 else None
        print_table(list_vpc_endpoints(vpc_id))
    elif cmd == "sg-rules" and len(sys.argv) >= 3:
        print(json.dumps(describe_sg_rules(sys.argv[2]), indent=2, ensure_ascii=False))
    elif cmd == "flow-logs":
        vpc_id = sys.argv[2] if len(sys.argv) >= 3 else None
        print_table(check_flow_logs(vpc_id))
    elif cmd in COMMANDS:
        print_table(COMMANDS[cmd][0]())
    else:
        print("사용법: python vpc_queries.py <명령> [인수]\n")
        for k, (_, desc) in COMMANDS.items():
            print(f"  {k:<20} {desc}")
        print("  vpc VPC_ID           VPC 상세")
        print("  subnets VPC_ID       서브넷 목록")
        print("  low-ip [THRESHOLD]   IP 고갈 위험 서브넷")
        print("  rtb VPC_ID           라우팅 테이블 목록")
        print("  routes RTB_ID        라우팅 경로 상세")
        print("  endpoints [VPC_ID]   VPC 엔드포인트 목록")
        print("  sg-rules SG_ID       보안 그룹 규칙 상세")
        print("  flow-logs [VPC_ID]   Flow Logs 활성화 여부")
