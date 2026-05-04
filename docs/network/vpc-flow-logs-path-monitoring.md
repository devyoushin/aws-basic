# DX → TGW → NLB → EC2 구간별 Flow Logs 모니터링

## 1. 개요

온프레미스에서 Direct Connect를 통해 AWS 내부의 EC2 애플리케이션까지 도달하는 경로에서
장애 발생 시 **"어느 구간에서 패킷이 사라졌는가"** 를 빠르게 좁히는 것이 핵심이다.

VPC Flow Logs만으로는 전체 경로를 커버할 수 없다. DX 구간은 CloudWatch 지표,
TGW 구간은 TGW Flow Logs, VPC 내부(NLB/EC2)는 VPC Flow Logs로 각각 다른 수집 체계를 갖는다.

```
온프레미스 서버
      │
      │ (전용선 - Layer 1/2)
      ▼
┌─────────────────────────┐
│  DX Connection          │  ← CloudWatch: ConnectionState, LightLevel
│  (Physical / LOA-CFA)   │
└──────────┬──────────────┘
           │ (Transit VIF - BGP)
           ▼
┌─────────────────────────┐
│  DX Gateway (DXGW)      │  ← CloudWatch: VirtualInterfaceState, BpsTx/Rx
└──────────┬──────────────┘
           │ (DXGW → TGW Association)
           ▼
┌─────────────────────────┐
│  Transit Gateway (TGW)  │  ← TGW Flow Logs, CloudWatch: PacketDropCount*
└──────────┬──────────────┘
           │ (TGW → VPC Attachment)
           ▼  ─── VPC 경계 (Flow Logs 시작) ───
┌─────────────────────────┐
│  NLB                    │  ← VPC Flow Logs (NLB ENI), CloudWatch: NLB 지표
│  (AZ당 ENI 1개)         │
└──────────┬──────────────┘
           │ (Target Group → EC2, Client IP 보존)
           ▼
┌─────────────────────────┐
│  EC2 Instance           │  ← VPC Flow Logs (EC2 ENI), Security Group ACCEPT/REJECT
└─────────────────────────┘
```

> **핵심 구분**: DX/DXGW 구간은 VPC 외부이므로 Flow Logs가 존재하지 않는다.
> TGW Flow Logs는 VPC Flow Logs와 별개 리소스이며, 각각 독립적으로 활성화해야 한다.

---

## 2. 설명

### 2.1 구간별 모니터링 수집 포인트

#### 구간 ① — DX Connection / VIF (VPC 외부)

| 지표 | CloudWatch 네임스페이스 | 설명 |
|------|------------------------|------|
| `ConnectionState` | `AWS/DX` | 물리 링크 Up(1)/Down(0) |
| `ConnectionLightLevelTx/Rx` | `AWS/DX` | 광신호 강도 (dBm) |
| `VirtualInterfaceBpsTx/Rx` | `AWS/DX` | VIF 대역폭 사용량 (bps) |
| `VirtualInterfaceState` | `AWS/DX` | BGP 세션 Up(1)/Down(0) |

- Flow Logs **없음** — 패킷 레벨 분석은 CloudWatch Network Synthetic Monitor (CWNM) 활용
- `ConnectionState=1`, `VirtualInterfaceState=0` → 물리 OK, BGP 설정 이상

#### 구간 ② — Transit Gateway

| 지표 / 로그 | 설명 |
|-------------|------|
| TGW Flow Logs | Attachment 단위 트래픽 기록 (VPC Flow Logs와 별개) |
| `BytesIn / BytesOut` | TGW를 통해 처리된 트래픽 양 |
| `PacketDropCountBlackhole` | Blackhole 라우트로 인한 드롭 — 라우팅 누락 |
| `PacketDropCountNoRoute` | 매칭 라우트 없음 — CIDR 미등록 |

TGW Flow Logs 레코드에는 `transit-gateway-id`, `transit-gateway-attachment-id` 필드가 추가되어
어느 Attachment(DX / VPC)에서 들어와서 어느 Attachment로 나갔는지 추적 가능하다.

#### 구간 ③ — NLB (VPC Flow Logs 대상)

NLB는 AZ당 ENI를 하나씩 가진다. VPC Flow Logs에서 `interface-id`가 NLB의 ENI ID이면
**온프레미스 클라이언트 IP → NLB ENI** 구간의 트래픽이다.

```
# NLB ENI 목록 확인
aws ec2 describe-network-interfaces \
  --filters "Name=description,Values=ELB net/nlb-prod-*" \
  --query 'NetworkInterfaces[*].{ENI:NetworkInterfaceId,AZ:AvailabilityZone,IP:PrivateIpAddress}'
```

| 지표 | CloudWatch 네임스페이스 | 설명 |
|------|------------------------|------|
| `ActiveFlowCount` | `AWS/NetworkELB` | 현재 활성 TCP 연결 수 |
| `NewFlowCount` | `AWS/NetworkELB` | 초당 신규 연결 수 |
| `ProcessedBytes` | `AWS/NetworkELB` | 처리된 바이트 (비용 기준) |
| `HealthyHostCount` | `AWS/NetworkELB` | 정상 타겟 수 (0이면 즉시 알람) |
| `UnHealthyHostCount` | `AWS/NetworkELB` | 비정상 타겟 수 |
| `TCP_Client_Reset_Count` | `AWS/NetworkELB` | 클라이언트가 RST 보낸 횟수 |
| `TCP_Target_Reset_Count` | `AWS/NetworkELB` | 타겟(EC2)이 RST 보낸 횟수 |

> **NLB Client IP Preservation**: NLB는 기본적으로 원본 클라이언트 IP를 보존한다.
> EC2의 Flow Logs에서 `srcaddr`이 온프레미스 IP(예: 192.168.x.x)로 보이는 것이 정상이다.
> NLB IP가 srcaddr로 보이면 Proxy Protocol 또는 Cross-Zone 로드밸런싱 경로일 수 있다.

#### 구간 ④ — EC2 Instance (VPC Flow Logs 대상)

EC2 ENI에서 수집되는 Flow Logs는 Security Group 적용 후 결과를 반영한다.

| `action` 값 | 의미 |
|-------------|------|
| `ACCEPT` | SG 인바운드 룰 허용 → 앱에 전달 |
| `REJECT` | SG 또는 NACL에서 차단 |

TCP flags 필드(v3+)로 세션 상태 추적:
| tcp_flags | 의미 |
|-----------|------|
| `2` (SYN) | 연결 시도 |
| `18` (SYN+ACK) | 서버 응답 |
| `1` (FIN) | 정상 종료 |
| `4` (RST) | 강제 종료 (앱 오류, 포트 미열림) |

---

### 2.2 각 구간 Flow Logs / 모니터링 활성화 (Terraform)

```hcl
# ────────────────────────────────────────────
# ① S3 버킷 (전 구간 공용)
# ────────────────────────────────────────────
resource "aws_s3_bucket" "network_logs" {
  bucket = "network-path-logs-${var.account_id}-${var.region}"
}

resource "aws_s3_bucket_lifecycle_configuration" "network_logs" {
  bucket = aws_s3_bucket.network_logs.id

  rule {
    id     = "transition-and-expire"
    status = "Enabled"

    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }

    expiration {
      days = 365
    }
  }
}

# ────────────────────────────────────────────
# ② TGW Flow Logs — Attachment별 분석 가능
# ────────────────────────────────────────────
resource "aws_flow_log" "tgw" {
  transit_gateway_id   = aws_ec2_transit_gateway.main.id
  log_destination_type = "s3"
  log_destination      = "${aws_s3_bucket.network_logs.arn}/tgw/"
  traffic_type         = "ALL"

  # TGW 전용 추가 필드: attachment-id, src/dst-vpc-account-id 등
  log_format = "$${version} $${account-id} $${interface-id} $${srcaddr} $${dstaddr} $${srcport} $${dstport} $${protocol} $${packets} $${bytes} $${start} $${end} $${action} $${log-status} $${transit-gateway-id} $${transit-gateway-attachment-id} $${transit-gateway-src-vpc-account-id} $${transit-gateway-dst-vpc-account-id}"
}

# ────────────────────────────────────────────
# ③ VPC Flow Logs — NLB/EC2 구간 커버
# ────────────────────────────────────────────
resource "aws_flow_log" "vpc_s3" {
  vpc_id               = aws_vpc.main.id
  traffic_type         = "ALL"
  log_destination_type = "s3"
  log_destination      = "${aws_s3_bucket.network_logs.arn}/vpc/"

  # tcp-flags, pkt-src/dstaddr 필드 포함 (v5)
  log_format = "$${version} $${account-id} $${interface-id} $${srcaddr} $${dstaddr} $${srcport} $${dstport} $${protocol} $${packets} $${bytes} $${start} $${end} $${action} $${log-status} $${vpc-id} $${subnet-id} $${instance-id} $${tcp-flags} $${pkt-srcaddr} $${pkt-dstaddr}"
}

# REJECT만 CloudWatch Logs로 (실시간 알람용)
resource "aws_cloudwatch_log_group" "vpc_reject" {
  name              = "/aws/vpc/flow-logs/reject"
  retention_in_days = 14
}

resource "aws_flow_log" "vpc_cw_reject" {
  vpc_id          = aws_vpc.main.id
  traffic_type    = "REJECT"
  iam_role_arn    = aws_iam_role.flow_logs.arn
  log_destination = aws_cloudwatch_log_group.vpc_reject.arn
}

# ────────────────────────────────────────────
# ④ DX 알람 (Flow Logs 대신 CloudWatch 지표)
# ────────────────────────────────────────────
resource "aws_cloudwatch_metric_alarm" "dx_connection_down" {
  alarm_name          = "dx-connection-down"
  comparison_operator = "LessThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "ConnectionState"
  namespace           = "AWS/DX"
  period              = 60
  statistic           = "Minimum"
  threshold           = 0

  dimensions = {
    ConnectionId = var.dx_connection_id
  }

  alarm_description = "DX 물리 링크 Down 감지"
  alarm_actions     = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "dx_vif_bgp_down" {
  alarm_name          = "dx-vif-bgp-down"
  comparison_operator = "LessThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "VirtualInterfaceState"
  namespace           = "AWS/DX"
  period              = 60
  statistic           = "Minimum"
  threshold           = 0

  dimensions = {
    VirtualInterfaceId = var.dx_transit_vif_id
  }

  alarm_description = "DX BGP 세션 Down 감지"
  alarm_actions     = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "tgw_blackhole_drop" {
  alarm_name          = "tgw-blackhole-drop"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "PacketDropCountBlackhole"
  namespace           = "AWS/TransitGateway"
  period              = 300
  statistic           = "Sum"
  threshold           = 0

  dimensions = {
    TransitGateway = aws_ec2_transit_gateway.main.id
  }

  alarm_description = "TGW Blackhole 라우트 드롭 발생 — 라우팅 테이블 점검 필요"
  alarm_actions     = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "nlb_unhealthy_host" {
  alarm_name          = "nlb-no-healthy-targets"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  metric_name         = "HealthyHostCount"
  namespace           = "AWS/NetworkELB"
  period              = 60
  statistic           = "Minimum"
  threshold           = 1

  dimensions = {
    LoadBalancer = aws_lb.nlb.arn_suffix
    TargetGroup  = aws_lb_target_group.app_tg.arn_suffix
  }

  alarm_description = "NLB 정상 타겟 없음 — EC2 앱 점검 필요"
  alarm_actions     = [aws_sns_topic.alerts.arn]
}
```

---

### 2.3 Athena — 구간별 트래픽 분석 쿼리

**Athena 테이블 생성 (VPC + TGW 공용, Partition Projection)**

```sql
-- VPC Flow Logs 테이블
CREATE EXTERNAL TABLE vpc_flow_logs (
  version      int,
  account_id   string,
  interface_id string,
  srcaddr      string,
  dstaddr      string,
  srcport      int,
  dstport      int,
  protocol     bigint,
  packets      bigint,
  bytes        bigint,
  start        bigint,
  end          bigint,
  action       string,
  log_status   string,
  vpc_id       string,
  subnet_id    string,
  instance_id  string,
  tcp_flags    int,
  pkt_srcaddr  string,
  pkt_dstaddr  string
)
PARTITIONED BY (partition_date string)
ROW FORMAT DELIMITED FIELDS TERMINATED BY ' '
STORED AS TEXTFILE
LOCATION 's3://network-path-logs-123456789012-ap-northeast-2/vpc/AWSLogs/123456789012/vpcflowlogs/ap-northeast-2/'
TBLPROPERTIES (
  "skip.header.line.count"="1",
  "projection.enabled"="true",
  "projection.partition_date.type"="date",
  "projection.partition_date.range"="2024/01/01,NOW",
  "projection.partition_date.format"="yyyy/MM/dd",
  "storage.location.template"="s3://network-path-logs-123456789012-ap-northeast-2/vpc/AWSLogs/123456789012/vpcflowlogs/ap-northeast-2/${partition_date}"
);

-- TGW Flow Logs 테이블
CREATE EXTERNAL TABLE tgw_flow_logs (
  version                              int,
  account_id                           string,
  interface_id                         string,
  srcaddr                              string,
  dstaddr                              string,
  srcport                              int,
  dstport                              int,
  protocol                             bigint,
  packets                              bigint,
  bytes                                bigint,
  start                                bigint,
  end                                  bigint,
  action                               string,
  log_status                           string,
  transit_gateway_id                   string,
  transit_gateway_attachment_id        string,
  transit_gateway_src_vpc_account_id   string,
  transit_gateway_dst_vpc_account_id   string
)
PARTITIONED BY (partition_date string)
ROW FORMAT DELIMITED FIELDS TERMINATED BY ' '
STORED AS TEXTFILE
LOCATION 's3://network-path-logs-123456789012-ap-northeast-2/tgw/'
TBLPROPERTIES (
  "skip.header.line.count"="1",
  "projection.enabled"="true",
  "projection.partition_date.type"="date",
  "projection.partition_date.range"="2024/01/01,NOW",
  "projection.partition_date.format"="yyyy/MM/dd",
  "storage.location.template"="s3://network-path-logs-123456789012-ap-northeast-2/tgw/${partition_date}"
);
```

**구간별 트러블슈팅 쿼리**

```sql
-- ① 특정 온프레미스 IP(192.168.1.100)의 트래픽이 TGW를 통과했는지 확인
SELECT
  from_unixtime(start)          AS time,
  srcaddr, dstaddr,
  srcport, dstport,
  packets, bytes, action,
  transit_gateway_attachment_id  AS attachment
FROM tgw_flow_logs
WHERE partition_date >= '2024/01/15'
  AND (srcaddr = '192.168.1.100' OR dstaddr = '192.168.1.100')
ORDER BY start DESC
LIMIT 50;

-- ② NLB ENI에서 온프레미스 → NLB 구간 흐름 확인
--    (interface_id를 NLB ENI ID로 교체)
SELECT
  from_unixtime(start)  AS time,
  srcaddr, dstaddr,
  dstport, packets, bytes,
  action, tcp_flags
FROM vpc_flow_logs
WHERE partition_date >= '2024/01/15'
  AND interface_id IN ('eni-nlb-az1-xxxx', 'eni-nlb-az2-xxxx')
  AND srcaddr = '192.168.1.100'   -- 온프레미스 클라이언트 IP
ORDER BY start DESC
LIMIT 50;

-- ③ EC2 ENI에서 해당 세션이 ACCEPT/REJECT 되었는지 확인
SELECT
  from_unixtime(start)  AS time,
  srcaddr, dstaddr,
  srcport, dstport,
  action, tcp_flags,
  instance_id
FROM vpc_flow_logs
WHERE partition_date >= '2024/01/15'
  AND instance_id = 'i-0123456789abcdef0'
  AND srcaddr = '192.168.1.100'
ORDER BY start DESC
LIMIT 50;

-- ④ EC2에서 RST(tcp_flags=4) 응답 패턴 탐지 — 앱 오류 or 포트 미열림
SELECT
  from_unixtime(start)  AS time,
  srcaddr, dstaddr,
  dstport,
  count(*) AS rst_count
FROM vpc_flow_logs
WHERE partition_date >= '2024/01/15'
  AND tcp_flags = 4    -- RST
  AND instance_id != '-'
GROUP BY 1, 2, 3, 4
ORDER BY rst_count DESC
LIMIT 20;

-- ⑤ NLB → EC2 구간 REJECT 확인 (Security Group 차단 의심)
SELECT
  interface_id,
  srcaddr,
  dstport,
  count(*) AS reject_count
FROM vpc_flow_logs
WHERE partition_date >= '2024/01/15'
  AND action = 'REJECT'
  AND srcaddr LIKE '10.%'    -- NLB 내부 IP에서 온 트래픽 (Cross-Zone 경유 시)
GROUP BY 1, 2, 3
ORDER BY reject_count DESC
LIMIT 20;

-- ⑥ TGW PacketDropCount 시간대별 분석 (Athena 대신 CloudWatch Metrics Insights 활용 가능)
SELECT
  date_format(from_unixtime(start), '%Y-%m-%d %H:%i') AS minute,
  sum(packets)  AS total_packets,
  sum(bytes)    AS total_bytes,
  count(CASE WHEN action = 'REJECT' THEN 1 END) AS reject_records
FROM tgw_flow_logs
WHERE partition_date >= '2024/01/15'
GROUP BY 1
ORDER BY 1;
```

---

## 3. 트러블슈팅

### 3.1 구간별 장애 시나리오

#### 장애 시나리오 A: DX 구간 — 연결 간헐적 끊김

**증상**: 온프레미스에서 AWS 접근이 수분마다 끊겼다 복구됨
**확인 순서**:

```bash
# 1. DX Connection 상태 확인 (최근 1시간 CloudWatch 데이터)
aws cloudwatch get-metric-statistics \
  --namespace AWS/DX \
  --metric-name ConnectionState \
  --dimensions Name=ConnectionId,Value=dxcon-xxxxxxxx \
  --start-time $(date -u -v-1H +%Y-%m-%dT%H:%M:%S)Z \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S)Z \
  --period 60 \
  --statistics Minimum \
  --region ap-northeast-2

# 2. 광신호 강도 확인 (정상 범위: -14.4 ~ +0.5 dBm)
aws cloudwatch get-metric-statistics \
  --namespace AWS/DX \
  --metric-name ConnectionLightLevelRx \
  --dimensions Name=ConnectionId,Value=dxcon-xxxxxxxx \
  --start-time $(date -u -v-1H +%Y-%m-%dT%H:%M:%S)Z \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S)Z \
  --period 60 \
  --statistics Average

# 3. BGP 세션 상태 (VIF ID 확인 필요)
aws directconnect describe-virtual-interfaces \
  --query 'virtualInterfaces[*].{VIF:virtualInterfaceId,State:virtualInterfaceState,BGP:bgpPeers}'
```

**원인 구분**:
- `ConnectionState` 0→1 반복 + `LightLevelRx` 낮음 → 광케이블 또는 트랜시버 물리 문제 → AWS 측 티켓
- `ConnectionState=1` + `VirtualInterfaceState=0` → BGP 세션 이상 → ASN/Password/IP 불일치 확인

---

#### 장애 시나리오 B: TGW 구간 — 특정 CIDR 통신 불가

**증상**: 온프레미스 특정 대역(192.168.10.0/24)에서만 EC2 접근 불가
**확인 순서**:

```bash
# 1. TGW 라우팅 테이블에 해당 CIDR 라우트 있는지 확인
aws ec2 search-transit-gateway-routes \
  --transit-gateway-route-table-id tgw-rtb-xxxxxxxx \
  --filters "Name=state,Values=active,blackhole"

# 2. DX Attachment의 전파 CIDR 확인 (온프레미스에서 BGP로 광고한 prefix)
aws ec2 describe-transit-gateway-attachments \
  --filters "Name=transit-gateway-id,Values=tgw-xxxxxxxx" \
  --query 'TransitGatewayAttachments[*].{Type:ResourceType,State:State,ID:TransitGatewayAttachmentId}'

# 3. PacketDropCountNoRoute 지표로 드롭 발생 여부 확인
aws cloudwatch get-metric-statistics \
  --namespace AWS/TransitGateway \
  --metric-name PacketDropCountNoRoute \
  --dimensions Name=TransitGateway,Value=tgw-xxxxxxxx \
  --start-time $(date -u -v-1H +%Y-%m-%dT%H:%M:%S)Z \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S)Z \
  --period 300 --statistics Sum
```

**Athena로 TGW 드롭 확인**:

```sql
-- TGW Flow Logs에서 해당 CIDR 트래픽이 수신은 됐지만 forwarding 안 된 경우 확인
-- (TGW Flow Logs는 수신 기준으로 기록, REJECT는 라우팅 불가 = 드롭)
SELECT
  srcaddr, dstaddr, dstport,
  transit_gateway_attachment_id,
  action,
  count(*) AS cnt
FROM tgw_flow_logs
WHERE partition_date >= '2024/01/15'
  AND srcaddr LIKE '192.168.10.%'
GROUP BY 1, 2, 3, 4, 5
ORDER BY cnt DESC;
```

---

#### 장애 시나리오 C: NLB 구간 — 연결은 되지만 일부 요청 타임아웃

**증상**: 온프레미스에서 NLB로의 연결은 성립하나 응답이 지연/타임아웃
**확인 순서**:

```bash
# 1. NLB 헬스체크 실패 타겟 확인
aws elbv2 describe-target-health \
  --target-group-arn arn:aws:elasticloadbalancing:ap-northeast-2:123456789012:targetgroup/app-tg/xxxx \
  --query 'TargetHealthDescriptions[*].{Target:Target.Id,State:TargetHealth.State,Reason:TargetHealth.Reason}'

# 2. NLB 속성 확인 (Connection Draining, Idle Timeout)
aws elbv2 describe-load-balancer-attributes \
  --load-balancer-arn arn:aws:elasticloadbalancing:ap-northeast-2:123456789012:loadbalancer/net/nlb-prod/xxxx

# 3. TCP_Target_Reset_Count 확인 (EC2가 RST를 많이 보내고 있는지)
aws cloudwatch get-metric-statistics \
  --namespace AWS/NetworkELB \
  --metric-name TCP_Target_Reset_Count \
  --dimensions Name=LoadBalancer,Value=net/nlb-prod/xxxx \
  --start-time $(date -u -v-1H +%Y-%m-%dT%H:%M:%S)Z \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S)Z \
  --period 60 --statistics Sum
```

**NLB 타임아웃 원인 체크리스트**:

| 증상 | 원인 | 조치 |
|------|------|------|
| `HealthyHostCount=0` | 앱 미기동, SG 차단 | EC2 앱 상태 확인, 8080 포트 SG 확인 |
| `TCP_Target_Reset_Count` 높음 | 앱이 연결 거부 (too many connections) | EC2 connection limit, ulimit 확인 |
| `ActiveFlowCount` 급증 | 연결 누수 (close 안 됨) | 앱 코드 connection pool 점검 |
| 헬스체크는 OK인데 타임아웃 | Cross-Zone 경유 시 AZ 불균형 | Cross-Zone Load Balancing 활성화 여부 확인 |

---

#### 장애 시나리오 D: EC2 구간 — Security Group REJECT

**증상**: NLB 헬스체크는 정상인데 특정 포트/IP에서 접근 불가
**확인 순서**:

```bash
# 1. EC2 ENI에서 REJECT 로그 확인 (CloudWatch Logs Insights)
# Log group: /aws/vpc/flow-logs/reject
fields @timestamp, srcaddr, dstaddr, dstport, action
| filter action = "REJECT"
| filter interface_id = "eni-0123456789abcdef0"
| sort @timestamp desc
| limit 50

# 2. 해당 EC2의 SG 인바운드 룰 확인
aws ec2 describe-security-groups \
  --group-ids sg-xxxxxxxx \
  --query 'SecurityGroups[*].IpPermissions[*].{Protocol:IpProtocol,From:FromPort,To:ToPort,CIDR:IpRanges[*].CidrIp}'

# 3. VPC NACL 확인 (SG가 허용해도 NACL에서 거부 가능)
aws ec2 describe-network-acls \
  --filters "Name=vpc-id,Values=vpc-xxxxxxxx" \
  --query 'NetworkAcls[*].Entries[?Egress==`false`]'
```

**Athena로 EC2 REJECT 패턴 분석**:

```sql
-- EC2 인스턴스에 도달하지 못하고 REJECT된 트래픽 분석
SELECT
  srcaddr,
  dstport,
  count(*) AS reject_count,
  sum(packets) AS total_packets
FROM vpc_flow_logs
WHERE partition_date >= '2024/01/15'
  AND action = 'REJECT'
  AND instance_id = 'i-0123456789abcdef0'
GROUP BY 1, 2
ORDER BY reject_count DESC
LIMIT 20;

-- tcp_flags로 SYN-only (연결 시도 → 응답 없음) 패턴 확인
-- tcp_flags=2는 SYN 패킷 (서버 응답 없이 클라이언트만 SYN 보내고 있음)
SELECT
  from_unixtime(start) AS time,
  srcaddr, dstport, tcp_flags,
  packets, action
FROM vpc_flow_logs
WHERE partition_date >= '2024/01/15'
  AND instance_id = 'i-0123456789abcdef0'
  AND tcp_flags = 2    -- SYN only: 서버가 응답 안 하는 상태
ORDER BY start DESC
LIMIT 30;
```

---

### 3.2 전체 경로 추적 (세션 단위 상관 분석)

특정 온프레미스 IP(192.168.1.100)의 세션이 어느 구간에서 끊겼는지 순서대로 확인한다.

```
Step 1: DX CloudWatch → ConnectionState / VirtualInterfaceState OK?
   ↓ (Yes)
Step 2: TGW Flow Logs → 해당 srcaddr 트래픽 수신 여부 확인
   ↓ (Yes)
Step 3: VPC Flow Logs (NLB ENI) → NLB까지 도달 여부
   ↓ (Yes)
Step 4: VPC Flow Logs (EC2 ENI) → action=ACCEPT 여부
   ↓ (REJECT면 → SG/NACL 점검, ACCEPT면 → 앱 레벨 문제)
Step 5: EC2 내부 → ss -tnp, netstat, 앱 로그 확인
```

```sql
-- 세션 추적 통합 쿼리 (VPC 내 구간)
-- srcaddr=온프레미스 IP, 특정 시간 기준으로 NLB ENI와 EC2 ENI 두 구간을 비교
SELECT
  interface_id,
  CASE
    WHEN interface_id IN ('eni-nlb-az1', 'eni-nlb-az2') THEN 'NLB'
    WHEN instance_id != '-' THEN 'EC2'
    ELSE 'Other'
  END AS segment,
  srcaddr, dstaddr, dstport,
  action, tcp_flags,
  from_unixtime(start) AS time
FROM vpc_flow_logs
WHERE partition_date = '2024/01/15'
  AND (srcaddr = '192.168.1.100' OR pkt_srcaddr = '192.168.1.100')
  AND start BETWEEN 1705276800 AND 1705280400   -- 특정 1시간 범위
ORDER BY start;
```

> **pkt_srcaddr vs srcaddr**: NLB가 Cross-Zone 로드밸런싱으로 다른 AZ로 트래픽을 보낼 때
> `srcaddr`은 NLB IP로 바뀌지만 `pkt_srcaddr`에는 원본 클라이언트 IP가 보존된다(v5 필드).

---

## 4. 모니터링 및 알람

```hcl
# CloudWatch 대시보드 — 전체 경로 한 눈에 보기
resource "aws_cloudwatch_dashboard" "network_path" {
  dashboard_name = "DX-TGW-NLB-EC2-PathMonitoring"

  dashboard_body = jsonencode({
    widgets = [
      # Row 1: DX 구간
      {
        type   = "metric"
        width  = 6
        height = 4
        properties = {
          title  = "① DX - Connection State"
          metrics = [["AWS/DX", "ConnectionState", "ConnectionId", var.dx_connection_id]]
          period = 60
          stat   = "Minimum"
          view   = "timeSeries"
          yAxis  = { left = { min = 0, max = 1 } }
        }
      },
      {
        type   = "metric"
        width  = 6
        height = 4
        properties = {
          title  = "① DX - VIF BGP State"
          metrics = [["AWS/DX", "VirtualInterfaceState", "VirtualInterfaceId", var.dx_transit_vif_id]]
          period = 60
          stat   = "Minimum"
        }
      },
      # Row 2: TGW 구간
      {
        type   = "metric"
        width  = 6
        height = 4
        properties = {
          title  = "② TGW - Packet Drop (Blackhole + NoRoute)"
          metrics = [
            ["AWS/TransitGateway", "PacketDropCountBlackhole", "TransitGateway", var.tgw_id],
            ["AWS/TransitGateway", "PacketDropCountNoRoute",   "TransitGateway", var.tgw_id]
          ]
          period = 300
          stat   = "Sum"
        }
      },
      # Row 3: NLB 구간
      {
        type   = "metric"
        width  = 6
        height = 4
        properties = {
          title  = "③ NLB - Healthy Host Count"
          metrics = [["AWS/NetworkELB", "HealthyHostCount",
            "LoadBalancer", var.nlb_arn_suffix,
            "TargetGroup",  var.tg_arn_suffix]]
          period = 60
          stat   = "Minimum"
        }
      },
      {
        type   = "metric"
        width  = 6
        height = 4
        properties = {
          title  = "③ NLB - TCP Reset Count"
          metrics = [
            ["AWS/NetworkELB", "TCP_Client_Reset_Count", "LoadBalancer", var.nlb_arn_suffix],
            ["AWS/NetworkELB", "TCP_Target_Reset_Count", "LoadBalancer", var.nlb_arn_suffix]
          ]
          period = 60
          stat   = "Sum"
        }
      }
    ]
  })
}
```

---

## 5. TIP

- **TGW Flow Logs ≠ VPC Flow Logs**: TGW는 VPC 외부의 네트워크 장치이므로 반드시 별도로 활성화해야 한다. VPC Flow Logs만 켜면 TGW 구간 드롭을 볼 수 없다.
- **NLB ENI ID 목록 미리 확보**: Flow Logs 분석 시 `interface_id`로 NLB/EC2 구간을 구분한다. 배포 초기에 NLB ENI ID를 태그나 파라미터 스토어에 기록해두면 장애 시 빠른 필터링이 가능하다.
- **pkt_srcaddr 필드 (v5)**: NLB가 중간에 있으면 EC2 ENI의 `srcaddr`이 NLB IP로 나온다. 온프레미스 원본 IP를 추적하려면 `pkt_srcaddr` 필드가 필수 — log_format에 반드시 포함할 것.
- **tcp_flags로 빠른 패턴 진단**: `tcp_flags=2` (SYN only) → 서버 응답 없음 (포트 닫힘 or SG 차단). `tcp_flags=4` (RST) → 앱이 연결 거부. `tcp_flags=1` (FIN) → 정상 종료.
- **VPC Reachability Analyzer**: 장애 재현이 어려울 때 사전 경로 검증 도구로 활용. DX VIF에서 특정 EC2까지 경로를 시뮬레이션하여 라우팅/SG 이슈를 미리 탐지한다.
- **CloudWatch Network Monitor (CWNM)**: DX 구간의 패킷 손실률과 RTT를 ms 단위로 측정 가능. Flow Logs로는 보이지 않는 L3/L4 레이턴시 분석에 유용하다.
