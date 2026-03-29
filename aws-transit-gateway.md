# AWS Transit Gateway (TGW)

## 1. 개요

Transit Gateway는 여러 VPC, 온프레미스(VPN/DX), 다른 AWS 계정을 중앙 허브 방식으로 연결하는 네트워크 라우팅 서비스다.
기존 VPC Peering의 N:N 메시 구조 한계를 해결하며, 라우팅 테이블 분리로 환경별 트래픽 격리(Hub-and-Spoke)가 가능하다.
멀티 계정, 멀티 VPC 아키텍처에서 네트워크 복잡도를 크게 줄여준다.

---

## 2. 설명

### 2.1 핵심 개념

**VPC Peering vs Transit Gateway**

| 항목 | VPC Peering | Transit Gateway |
|------|------------|----------------|
| 연결 구조 | 1:1 (메시) | N:1 (허브) |
| VPC 10개 연결 | 45개 Peering 필요 | 10개 Attachment |
| 전이적 라우팅 | 불가 (A→B→C 불가) | 가능 (TGW가 중계) |
| 리전 간 연결 | Inter-Region Peering | Inter-Region TGW Peering |
| 대역폭 한도 | 없음 | TGW당 50Gbps 버스트 |
| 비용 | Attachment 없음, 데이터 처리만 | $0.07/Attachment/시간 + $0.02/GB |
| 관리 복잡도 | VPC 증가할수록 급증 | TGW 라우팅 테이블로 중앙 관리 |

**Hub-and-Spoke 아키텍처**

```
                     ┌─────────────────┐
                     │  Transit Gateway │
                     └──────┬──────────┘
          ┌─────────────────┼──────────────────┐
          │                 │                  │
    ┌─────▼─────┐    ┌──────▼──────┐    ┌──────▼──────┐
    │ Prod VPC  │    │  Dev VPC   │    │ Shared VPC  │
    │(10.10/16) │    │(10.20/16)  │    │(10.0/16)    │
    └───────────┘    └────────────┘    │ - VPN/DX    │
                                       │ - DNS       │
                                       │ - Egress    │
                                       └─────────────┘
```

**TGW 라우팅 테이블로 환경 격리**

```
TGW 라우팅 테이블 1: "prod-rt"
  - Prod VPC Attachment: Propagate
  - Shared VPC Attachment: Static 추가
  - Dev VPC: 연결 안 함 (Prod↔Dev 격리)

TGW 라우팅 테이블 2: "dev-rt"
  - Dev VPC Attachment: Propagate
  - Shared VPC Attachment: Static 추가
  - Prod VPC: 연결 안 함

TGW 라우팅 테이블 3: "shared-rt"
  - Prod + Dev + Shared 모두 Propagate
  → Shared VPC에서는 모든 환경 접근 가능 (운영 도구)
```

---

### 2.2 실무 적용 코드

**Terraform — Transit Gateway 생성**

```hcl
resource "aws_ec2_transit_gateway" "main" {
  description = "Central TGW for multi-VPC connectivity"

  # BGP AS 번호 (온프레미스 VPN 연결 시 사용)
  amazon_side_asn = 64512

  # 자동 라우팅 전파 비활성화 (명시적 관리 권장)
  default_route_table_association = "disable"
  default_route_table_propagation = "disable"

  # 멀티캐스트 (필요 시만 활성화)
  multicast_support = "disable"

  # VPN ECMP: VPN 연결 다중 경로 허용
  vpn_ecmp_support = "enable"

  # DNS 지원 (VPC 간 Route 53 Resolver 연동 시)
  dns_support = "enable"

  tags = { Name = "main-tgw" }
}

# 환경별 TGW 라우팅 테이블
resource "aws_ec2_transit_gateway_route_table" "prod" {
  transit_gateway_id = aws_ec2_transit_gateway.main.id
  tags               = { Name = "tgw-rt-prod" }
}

resource "aws_ec2_transit_gateway_route_table" "dev" {
  transit_gateway_id = aws_ec2_transit_gateway.main.id
  tags               = { Name = "tgw-rt-dev" }
}

resource "aws_ec2_transit_gateway_route_table" "shared" {
  transit_gateway_id = aws_ec2_transit_gateway.main.id
  tags               = { Name = "tgw-rt-shared" }
}
```

**Terraform — VPC Attachment + 라우팅 연결**

```hcl
# Prod VPC TGW Attachment
resource "aws_ec2_transit_gateway_vpc_attachment" "prod" {
  transit_gateway_id = aws_ec2_transit_gateway.main.id
  vpc_id             = aws_vpc.prod.id
  subnet_ids         = aws_subnet.prod_private[*].id   # Private subnet에 연결

  transit_gateway_default_route_table_association = false
  transit_gateway_default_route_table_propagation = false

  tags = { Name = "tgw-attach-prod" }
}

# Prod Attachment → Prod 라우팅 테이블에 연결
resource "aws_ec2_transit_gateway_route_table_association" "prod" {
  transit_gateway_attachment_id  = aws_ec2_transit_gateway_vpc_attachment.prod.id
  transit_gateway_route_table_id = aws_ec2_transit_gateway_route_table.prod.id
}

# Prod VPC CIDR을 Prod 라우팅 테이블에 전파
resource "aws_ec2_transit_gateway_route_table_propagation" "prod_to_prod" {
  transit_gateway_attachment_id  = aws_ec2_transit_gateway_vpc_attachment.prod.id
  transit_gateway_route_table_id = aws_ec2_transit_gateway_route_table.prod.id
}

# Prod VPC CIDR을 Shared 라우팅 테이블에도 전파 (Shared VPC에서 Prod 접근 가능)
resource "aws_ec2_transit_gateway_route_table_propagation" "prod_to_shared" {
  transit_gateway_attachment_id  = aws_ec2_transit_gateway_vpc_attachment.prod.id
  transit_gateway_route_table_id = aws_ec2_transit_gateway_route_table.shared.id
}

# Prod VPC 라우팅 테이블에 TGW 경유 라우트 추가
resource "aws_route" "prod_to_tgw" {
  count                  = length(aws_route_table.prod_private)
  route_table_id         = aws_route_table.prod_private[count.index].id
  destination_cidr_block = "10.0.0.0/8"   # 전체 사내 IP → TGW로
  transit_gateway_id     = aws_ec2_transit_gateway.main.id
}
```

**멀티 계정 TGW 공유 (RAM — Resource Access Manager)**

```hcl
# 네트워크 계정에서 TGW를 다른 계정과 공유
resource "aws_ram_resource_share" "tgw" {
  name                      = "tgw-share"
  allow_external_principals = false   # Organization 내부만
}

resource "aws_ram_resource_association" "tgw" {
  resource_arn       = aws_ec2_transit_gateway.main.arn
  resource_share_arn = aws_ram_resource_share.tgw.arn
}

resource "aws_ram_principal_association" "tgw" {
  principal          = "arn:aws:organizations::123456789012:organization/o-xxxxxxxxxx"
  resource_share_arn = aws_ram_resource_share.tgw.arn
}

# 다른 계정(수신 측)에서 Attachment 생성
# → 다른 계정의 Terraform에서 transit_gateway_id로 기존 TGW ARN 참조
data "aws_ec2_transit_gateway" "shared" {
  filter {
    name   = "tag:Name"
    values = ["main-tgw"]
  }
}
```

**VPN 연결 (온프레미스)**

```hcl
resource "aws_customer_gateway" "onprem" {
  bgp_asn    = 65000   # 온프레미스 라우터 AS 번호
  ip_address = "203.0.113.10"   # 온프레미스 공인 IP
  type       = "ipsec.1"

  tags = { Name = "onprem-cgw" }
}

resource "aws_vpn_connection" "onprem" {
  transit_gateway_id  = aws_ec2_transit_gateway.main.id
  customer_gateway_id = aws_customer_gateway.onprem.id
  type                = "ipsec.1"

  # ECMP를 위해 양쪽 터널 모두 활성화
  tunnel1_inside_cidr = "169.254.10.0/30"
  tunnel2_inside_cidr = "169.254.11.0/30"

  tags = { Name = "vpn-onprem" }
}
```

---

### 2.3 보안/비용 Best Practice

- **Attachment 비용 주의**: Attachment당 $0.07/시간 = VPC 10개 × $0.07 × 720h = $504/월. 소규모 환경에서는 VPC Peering이 저렴
- **라우팅 테이블로 Prod/Dev 격리 필수**: 기본 라우팅 테이블 사용 금지. 환경별 별도 라우팅 테이블로 Prod↔Dev 직접 통신 차단
- **Blackhole 라우트로 CIDR 충돌 방지**: TGW 라우팅 테이블에 사용하지 않는 CIDR을 blackhole로 등록해 실수로 통신되지 않도록
- **TGW Flow Logs**: VPC Flow Logs와 별개로 TGW 레벨 Flow Logs를 활성화하면 Transit 트래픽 전체 감사 가능

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**VPC 간 통신이 안 됨 (Attachment는 있음)**

```bash
# 체크리스트 (순서대로 확인)
# 1. TGW Attachment 상태
aws ec2 describe-transit-gateway-vpc-attachments \
  --filters "Name=transit-gateway-id,Values=tgw-xxxxxxxx" \
  --query 'TransitGatewayVpcAttachments[*].{VPC:VpcId,State:State}'

# 2. TGW 라우팅 테이블에 목적지 CIDR 있는지 확인
aws ec2 search-transit-gateway-routes \
  --transit-gateway-route-table-id tgw-rtb-xxxxxxxx \
  --filters "Name=type,Values=propagated,static"

# 3. VPC 라우팅 테이블에 TGW 경유 라우트 있는지 확인
aws ec2 describe-route-tables \
  --filters "Name=vpc-id,Values=vpc-xxxxxxxx" \
  --query 'RouteTables[*].Routes[?TransitGatewayId!=null]'

# 4. 보안그룹에서 상대 CIDR 허용 여부 확인
```

**멀티 계정 Attachment 연결 대기 중**

```bash
# 공유 TGW에 다른 계정이 Attachment 생성 시 수락 대기 발생
# (auto_accept_shared_attachments = "enable" 설정 시 자동 수락)

# 대기 중인 Attachment 확인
aws ec2 describe-transit-gateway-vpc-attachments \
  --filters "Name=state,Values=pendingAcceptance"

# 수락
aws ec2 accept-transit-gateway-vpc-attachment \
  --transit-gateway-attachment-id tgw-attach-xxxxxxxx
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: TGW와 VPC Peering 중 어떤 걸 써야 하나요?**
A: VPC가 3개 이하이고 단순 연결이면 Peering이 저렴합니다. VPC가 많거나 온프레미스 연결, 중앙화된 Egress/Ingress, 환경 격리가 필요하면 TGW를 선택하세요.

**Q: 같은 TGW에 연결된 VPC끼리 CIDR이 겹쳐도 되나요?**
A: 안 됩니다. TGW는 라우팅 테이블 기반이므로 CIDR이 겹치면 올바른 목적지로 라우팅할 수 없습니다. VPC 설계 시 전사 IP 관리 계획이 필수입니다.

---

## 4. 모니터링 및 알람

```hcl
# TGW 패킷 드롭 알람
resource "aws_cloudwatch_metric_alarm" "tgw_packet_drop" {
  alarm_name          = "tgw-packet-drop-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "PacketDropCountBlackhole"
  namespace           = "AWS/TransitGateway"
  period              = 300
  statistic           = "Sum"
  threshold           = 100

  dimensions = {
    TransitGateway = aws_ec2_transit_gateway.main.id
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
}
```

**TGW Flow Logs 활성화**

```hcl
resource "aws_flow_log" "tgw" {
  transit_gateway_id   = aws_ec2_transit_gateway.main.id
  log_destination_type = "s3"
  log_destination      = "${aws_s3_bucket.flow_logs.arn}/tgw/"
  traffic_type         = "ALL"
}
```

---

## 5. TIP

- **Network Manager**: TGW 기반 글로벌 네트워크를 시각적으로 관리하는 서비스. 멀티 리전 TGW를 하나의 대시보드에서 모니터링 가능
- **TGW Connect**: SD-WAN 솔루션과 GRE 터널로 연결. VPN보다 대역폭이 넓고 BGP 지원
- **Egress VPC 패턴**: 모든 VPC의 인터넷 트래픽을 TGW → 중앙 Egress VPC → NAT GW로 집중. NAT GW 비용과 관리 포인트를 줄임
- **CIDR 블록 계획 필수**: TGW 사용 환경에서는 모든 VPC CIDR이 겹치지 않아야 함. `vpc-subnet-design.md`의 멀티 계정 IP 관리 참고
