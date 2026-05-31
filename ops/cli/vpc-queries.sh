#!/usr/bin/env bash
# VPC 실무 쿼리 모음
# 사용법: 필요한 함수만 복붙하거나, 전체 실행 시 ./vpc-queries.sh <명령>

REGION="${AWS_DEFAULT_REGION:-ap-northeast-2}"

# ─── VPC 조회 ─────────────────────────────────────────────────────────────────

# 모든 VPC 목록 (ID, CIDR, 이름, default 여부)
list_vpcs() {
  aws ec2 describe-vpcs \
    --region "$REGION" \
    --query 'Vpcs[].[
      VpcId,
      CidrBlock,
      Tags[?Key==`Name`].Value | [0],
      IsDefault,
      State
    ]' \
    --output table
}

# 특정 VPC 상세 (Secondary CIDR 포함)
describe_vpc() {
  local vpc_id="${1:?VPC ID를 입력하세요 (vpc-xxxxxxxx)}"

  aws ec2 describe-vpcs \
    --region "$REGION" \
    --vpc-ids "$vpc_id" \
    --query 'Vpcs[0]' \
    --output json
}

# ─── 서브넷 ───────────────────────────────────────────────────────────────────

# 특정 VPC의 서브넷 목록
list_subnets() {
  local vpc_id="${1:?VPC ID를 입력하세요}"

  aws ec2 describe-subnets \
    --region "$REGION" \
    --filters "Name=vpc-id,Values=${vpc_id}" \
    --query 'Subnets[].[
      SubnetId,
      CidrBlock,
      AvailabilityZone,
      Tags[?Key==`Name`].Value | [0],
      AvailableIpAddressCount,
      MapPublicIpOnLaunch
    ]' \
    --output table
}

# IP 고갈 위험 서브넷 탐지 (가용 IP < 임계값)
find_low_ip_subnets() {
  local threshold="${1:-20}"

  aws ec2 describe-subnets \
    --region "$REGION" \
    --query "Subnets[?AvailableIpAddressCount < \`${threshold}\`].[
      SubnetId,
      CidrBlock,
      AvailabilityZone,
      Tags[?Key==\`Name\`].Value | [0],
      AvailableIpAddressCount
    ]" \
    --output table
}

# ─── 라우팅 테이블 ────────────────────────────────────────────────────────────

# 특정 VPC의 라우팅 테이블 목록
list_route_tables() {
  local vpc_id="${1:?VPC ID를 입력하세요}"

  aws ec2 describe-route-tables \
    --region "$REGION" \
    --filters "Name=vpc-id,Values=${vpc_id}" \
    --query 'RouteTables[].[
      RouteTableId,
      Tags[?Key==`Name`].Value | [0],
      length(Associations)
    ]' \
    --output table
}

# 특정 라우팅 테이블의 경로 상세
describe_routes() {
  local rtb_id="${1:?라우팅 테이블 ID를 입력하세요 (rtb-xxxxxxxx)}"

  aws ec2 describe-route-tables \
    --region "$REGION" \
    --route-table-ids "$rtb_id" \
    --query 'RouteTables[0].Routes[].[
      DestinationCidrBlock,
      GatewayId,
      NatGatewayId,
      TransitGatewayId,
      VpcPeeringConnectionId,
      State
    ]' \
    --output table
}

# ─── 인터넷 게이트웨이 / NAT 게이트웨이 ──────────────────────────────────────

# IGW 목록
list_igw() {
  aws ec2 describe-internet-gateways \
    --region "$REGION" \
    --query 'InternetGateways[].[
      InternetGatewayId,
      Tags[?Key==`Name`].Value | [0],
      Attachments[0].VpcId,
      Attachments[0].State
    ]' \
    --output table
}

# NAT 게이트웨이 목록 및 상태
list_nat_gateways() {
  aws ec2 describe-nat-gateways \
    --region "$REGION" \
    --query 'NatGateways[].[
      NatGatewayId,
      Tags[?Key==`Name`].Value | [0],
      SubnetId,
      State,
      NatGatewayAddresses[0].PublicIp,
      CreateTime
    ]' \
    --output table
}

# ─── VPC 엔드포인트 ───────────────────────────────────────────────────────────

# VPC 엔드포인트 목록 (Gateway/Interface)
list_vpc_endpoints() {
  local vpc_id="${1:-}"
  local filter=""
  [[ -n "$vpc_id" ]] && filter="--filters Name=vpc-id,Values=${vpc_id}"

  # shellcheck disable=SC2086
  aws ec2 describe-vpc-endpoints \
    --region "$REGION" \
    $filter \
    --query 'VpcEndpoints[].[
      VpcEndpointId,
      ServiceName,
      VpcEndpointType,
      State,
      VpcId
    ]' \
    --output table
}

# ─── VPC 피어링 ───────────────────────────────────────────────────────────────

# VPC 피어링 연결 목록
list_peering_connections() {
  aws ec2 describe-vpc-peering-connections \
    --region "$REGION" \
    --query 'VpcPeeringConnections[].[
      VpcPeeringConnectionId,
      RequesterVpcInfo.VpcId,
      RequesterVpcInfo.CidrBlock,
      AccepterVpcInfo.VpcId,
      AccepterVpcInfo.CidrBlock,
      Status.Code
    ]' \
    --output table
}

# ─── 보안 그룹 ────────────────────────────────────────────────────────────────

# 특정 VPC의 보안 그룹 전체 목록
list_sg_in_vpc() {
  local vpc_id="${1:?VPC ID를 입력하세요}"

  aws ec2 describe-security-groups \
    --region "$REGION" \
    --filters "Name=vpc-id,Values=${vpc_id}" \
    --query 'SecurityGroups[].[GroupId, GroupName, Description]' \
    --output table
}

# 사용되지 않는 보안 그룹 탐지 (ENI에 연결되지 않은 것)
find_unused_security_groups() {
  echo "[모든 보안 그룹]"
  all_sgs=$(aws ec2 describe-security-groups \
    --region "$REGION" \
    --query 'SecurityGroups[].GroupId' \
    --output text)

  echo "[ENI에 연결된 보안 그룹]"
  used_sgs=$(aws ec2 describe-network-interfaces \
    --region "$REGION" \
    --query 'NetworkInterfaces[].Groups[].GroupId' \
    --output text | tr '\t' '\n' | sort -u)

  echo "[미사용 보안 그룹 (default 제외)]"
  for sg in $all_sgs; do
    if ! echo "$used_sgs" | grep -q "$sg"; then
      name=$(aws ec2 describe-security-groups \
        --region "$REGION" \
        --group-ids "$sg" \
        --query 'SecurityGroups[0].[GroupId, GroupName]' \
        --output text 2>/dev/null)
      echo "$name"
    fi
  done
}

# 보안 그룹 인바운드 규칙 상세 출력
describe_sg_rules() {
  local sg_id="${1:?보안 그룹 ID를 입력하세요 (sg-xxxxxxxx)}"

  echo "=== 인바운드 규칙 ==="
  aws ec2 describe-security-groups \
    --region "$REGION" \
    --group-ids "$sg_id" \
    --query 'SecurityGroups[0].IpPermissions[].[
      IpProtocol,
      FromPort,
      ToPort,
      IpRanges[*].CidrIp,
      UserIdGroupPairs[*].GroupId
    ]' \
    --output table

  echo "=== 아웃바운드 규칙 ==="
  aws ec2 describe-security-groups \
    --region "$REGION" \
    --group-ids "$sg_id" \
    --query 'SecurityGroups[0].IpPermissionsEgress[].[
      IpProtocol,
      FromPort,
      ToPort,
      IpRanges[*].CidrIp
    ]' \
    --output table
}

# ─── Flow Logs ────────────────────────────────────────────────────────────────

# VPC Flow Logs 활성화 여부 확인
check_flow_logs() {
  local vpc_id="${1:-}"
  local filter=""
  [[ -n "$vpc_id" ]] && filter="--filter Name=resource-id,Values=${vpc_id}"

  # shellcheck disable=SC2086
  aws ec2 describe-flow-logs \
    --region "$REGION" \
    $filter \
    --query 'FlowLogs[].[
      FlowLogId,
      ResourceId,
      TrafficType,
      DeliverLogsStatus,
      LogDestinationType,
      LogDestination
    ]' \
    --output table
}

# ─── 실행 진입점 ──────────────────────────────────────────────────────────────
case "${1:-}" in
  vpcs)              list_vpcs ;;
  vpc)               describe_vpc "$2" ;;
  subnets)           list_subnets "$2" ;;
  low-ip)            find_low_ip_subnets "${2:-20}" ;;
  rtb)               list_route_tables "$2" ;;
  routes)            describe_routes "$2" ;;
  igw)               list_igw ;;
  nat)               list_nat_gateways ;;
  endpoints)         list_vpc_endpoints "${2:-}" ;;
  peering)           list_peering_connections ;;
  sg-list)           list_sg_in_vpc "$2" ;;
  unused-sg)         find_unused_security_groups ;;
  sg-rules)          describe_sg_rules "$2" ;;
  flow-logs)         check_flow_logs "${2:-}" ;;
  *)
    echo "사용법: $0 <명령> [인수]"
    echo ""
    echo "  vpcs                  전체 VPC 목록"
    echo "  vpc VPC_ID            VPC 상세"
    echo "  subnets VPC_ID        서브넷 목록"
    echo "  low-ip [THRESHOLD]    IP 고갈 위험 서브넷 (기본: 20개 미만)"
    echo "  rtb VPC_ID            라우팅 테이블 목록"
    echo "  routes RTB_ID         라우팅 경로 상세"
    echo "  igw                   인터넷 게이트웨이 목록"
    echo "  nat                   NAT 게이트웨이 목록"
    echo "  endpoints [VPC_ID]    VPC 엔드포인트 목록"
    echo "  peering               VPC 피어링 목록"
    echo "  sg-list VPC_ID        VPC 내 보안 그룹 목록"
    echo "  unused-sg             미사용 보안 그룹 탐지"
    echo "  sg-rules SG_ID        보안 그룹 규칙 상세"
    echo "  flow-logs [VPC_ID]    Flow Logs 활성화 여부"
    ;;
esac
