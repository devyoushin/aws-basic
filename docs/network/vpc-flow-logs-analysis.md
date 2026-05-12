# VPC Flow Logs — 설정부터 Athena 분석까지

## 1. 개요

VPC Flow Logs는 ENI(Elastic Network Interface)를 통과하는 IP 트래픽 메타데이터를 캡처하는 관리형 서비스다. 패킷 페이로드는 포함하지 않으며, 5-tuple(src IP, dst IP, src port, dst port, protocol)과 accept/reject 판정, 바이트/패킷 카운터를 기록한다.

**수집 위치**: VPC, 서브넷, ENI 단위로 활성화 가능
**저장 대상**: S3 (대용량 분석), CloudWatch Logs (실시간 알람), Kinesis Data Firehose (스트리밍 변환)

---

## 2. 레코드 구조 — 버전별 필드

Flow Logs는 `version` 필드로 레코드 스키마를 식별한다. 기본값은 v2이며, 커스텀 포맷 지정 시 v3~v5 필드를 추가할 수 있다.

### 버전별 추가 필드

| 버전 | 추가 필드 | 실무 활용 |
|------|----------|----------|
| v2 (기본) | version, account-id, interface-id, srcaddr, dstaddr, srcport, dstport, protocol, packets, bytes, start, end, action, log-status | 기본 보안 감사 |
| v3 | vpc-id, subnet-id, instance-id, tcp-flags, type, pkt-srcaddr, pkt-dstaddr | NAT/LB 통과 실제 IP 추적 |
| v4 | region, az-id, sublocation-type, sublocation-id | Outpost/Wavelength 구분 |
| v5 | pkt-src-aws-service, pkt-dst-aws-service, flow-direction, traffic-path | AWS 서비스 트래픽 분류, ingress/egress 구분 |

### 핵심 필드 상세

```
srcaddr / dstaddr vs pkt-srcaddr / pkt-dstaddr
```

- **srcaddr/dstaddr**: 해당 ENI 관점의 IP. NLB ENI에서 캡처하면 srcaddr가 NLB IP가 됨
- **pkt-srcaddr/pkt-dstaddr** (v3+): 패킷 원본/최종 IP. NAT, LB 뒤에서도 실제 클라이언트 IP 추적 가능

```
tcp-flags (v3+)
```

TCP 플래그를 비트마스크로 기록한다:

| 값 | 플래그 | 의미 |
|----|--------|------|
| 1 | FIN | 연결 종료 |
| 2 | SYN | 연결 시작 |
| 4 | RST | 강제 종료 |
| 8 | PSH | 데이터 즉시 전달 |
| 16 | ACK | 확인 응답 |
| 18 | SYN+ACK | 3-way handshake 응답 |

SYN(2)만 있고 SYN+ACK(18)가 없으면 → SYN Flood 또는 방화벽 차단 의심

```
flow-direction (v5)
```
- `ingress`: ENI 기준 수신 트래픽
- `egress`: ENI 기준 송신 트래픽
- `unknown` 또는 `-`: 로컬 통신 (같은 호스트 내)

```
log-status
```

| 값 | 의미 | 대응 |
|----|------|------|
| `OK` | 정상 수집 | — |
| `NODATA` | 집계 구간 내 트래픽 없음 | 정상 |
| `SKIPDATA` | 내부 버퍼 초과로 레코드 누락 | 해당 시간대 분석 신뢰도 저하, ENI 단위 분산 검토 |

---

## 3. Flow Logs 설정

### 3.1 집계 인터벌 (Aggregation Interval)

Flow Logs는 기본 10분 단위로 집계한다. 1분으로 줄이면 실시간성은 높아지지만 로그 파일 수와 비용이 증가한다.

```
traffic_aggregation_interval = 60   # 1분 (최소값)
traffic_aggregation_interval = 600  # 10분 (기본값, 비용 효율)
```

**선택 기준**: 보안 이벤트 실시간 탐지 목적 → 1분, 장기 분석/비용 절감 → 10분

### 3.2 커스텀 로그 포맷

`log_format`을 지정하지 않으면 v2 기본 14개 필드만 기록된다. 실무에서는 v3~v5 필드를 추가해 분석력을 높인다.

```hcl
# 권장 커스텀 포맷 (v5 기준)
log_format = join(" ", [
  "$${version}",
  "$${account-id}",
  "$${interface-id}",
  "$${srcaddr}",
  "$${dstaddr}",
  "$${srcport}",
  "$${dstport}",
  "$${protocol}",
  "$${packets}",
  "$${bytes}",
  "$${start}",
  "$${end}",
  "$${action}",
  "$${log-status}",
  "$${vpc-id}",
  "$${subnet-id}",
  "$${instance-id}",
  "$${tcp-flags}",
  "$${pkt-srcaddr}",
  "$${pkt-dstaddr}",
  "$${flow-direction}",
  "$${traffic-path}"
])
```

> `$${}` 이중 달러 사인은 Terraform에서 리터럴 `${}` 이스케이프. 실제 Flow Logs 포맷은 `${version}` 형태로 저장됨

### 3.3 Terraform 전체 설정

```hcl
# ─────────────────────────────────────────
# S3 버킷 — Flow Logs 저장
# ─────────────────────────────────────────
resource "aws_s3_bucket" "flow_logs" {
  bucket        = "my-vpc-flow-logs-${data.aws_caller_identity.current.account_id}"
  force_destroy = false
}

resource "aws_s3_bucket_versioning" "flow_logs" {
  bucket = aws_s3_bucket.flow_logs.id
  versioning_configuration { status = "Disabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "flow_logs" {
  bucket = aws_s3_bucket.flow_logs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "flow_logs" {
  bucket = aws_s3_bucket.flow_logs.id

  rule {
    id     = "tiering"
    status = "Enabled"

    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }

    transition {
      days          = 90
      storage_class = "GLACIER_IR"
    }

    expiration {
      days = 365
    }
  }
}

# ─────────────────────────────────────────
# S3 버킷 정책 — Flow Logs 서비스 허용
# ─────────────────────────────────────────
resource "aws_s3_bucket_policy" "flow_logs" {
  bucket = aws_s3_bucket.flow_logs.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AWSLogDeliveryWrite"
        Effect = "Allow"
        Principal = {
          Service = "delivery.logs.amazonaws.com"
        }
        Action   = "s3:PutObject"
        Resource = "${aws_s3_bucket.flow_logs.arn}/AWSLogs/*"
        Condition = {
          StringEquals = {
            "s3:x-amz-acl"         = "bucket-owner-full-control"
            "aws:SourceAccount"    = data.aws_caller_identity.current.account_id
          }
        }
      },
      {
        Sid    = "AWSLogDeliveryAclCheck"
        Effect = "Allow"
        Principal = {
          Service = "delivery.logs.amazonaws.com"
        }
        Action   = "s3:GetBucketAcl"
        Resource = aws_s3_bucket.flow_logs.arn
      }
    ]
  })
}

# ─────────────────────────────────────────
# VPC Flow Log — S3 (전체 트래픽, 장기 분석)
# ─────────────────────────────────────────
resource "aws_flow_log" "s3_all" {
  vpc_id                          = aws_vpc.main.id
  traffic_type                    = "ALL"
  log_destination_type            = "s3"
  log_destination                 = aws_s3_bucket.flow_logs.arn
  max_aggregation_interval        = 600   # 10분 집계 (비용 효율)

  log_format = "$${version} $${account-id} $${interface-id} $${srcaddr} $${dstaddr} $${srcport} $${dstport} $${protocol} $${packets} $${bytes} $${start} $${end} $${action} $${log-status} $${vpc-id} $${subnet-id} $${instance-id} $${tcp-flags} $${pkt-srcaddr} $${pkt-dstaddr} $${flow-direction} $${traffic-path}"

  destination_options {
    file_format                = "plain-text"  # 또는 "parquet" (Firehose 없이 직접 Parquet)
    hive_compatible_partitions = true           # s3://...year=2024/month=01/day=15/
    per_hour_files             = true           # 시간별 파일 분리
  }
}

# ─────────────────────────────────────────
# VPC Flow Log — CloudWatch Logs (REJECT 실시간 알람용)
# ─────────────────────────────────────────
resource "aws_cloudwatch_log_group" "flow_logs_reject" {
  name              = "/aws/vpc/flow-logs/reject"
  retention_in_days = 14  # 단기 보관, 알람용
}

resource "aws_iam_role" "flow_logs_cw" {
  name = "vpc-flow-logs-cloudwatch-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "vpc-flow-logs.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = {
          "aws:SourceAccount" = data.aws_caller_identity.current.account_id
        }
        ArnLike = {
          "aws:SourceArn" = "arn:aws:ec2:*:${data.aws_caller_identity.current.account_id}:vpc-flow-log/*"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "flow_logs_cw" {
  role = aws_iam_role.flow_logs_cw.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "logs:DescribeLogGroups",
        "logs:DescribeLogStreams"
      ]
      Resource = "*"
    }]
  })
}

resource "aws_flow_log" "cloudwatch_reject" {
  vpc_id                   = aws_vpc.main.id
  traffic_type             = "REJECT"
  log_destination_type     = "cloud-watch-logs"
  log_destination          = aws_cloudwatch_log_group.flow_logs_reject.arn
  iam_role_arn             = aws_iam_role.flow_logs_cw.arn
  max_aggregation_interval = 60  # 1분 집계 (실시간 알람 목적)
}
```

### 3.4 AWS CLI로 설정

```bash
# ENI 단위 Flow Logs 활성화 (특정 인터페이스만 캡처)
aws ec2 create-flow-logs \
  --resource-type NetworkInterface \
  --resource-ids eni-0123456789abcdef0 \
  --traffic-type ALL \
  --log-destination-type s3 \
  --log-destination arn:aws:s3:::my-vpc-flow-logs-123456789012 \
  --max-aggregation-interval 60 \
  --log-format '${version} ${account-id} ${interface-id} ${srcaddr} ${dstaddr} ${srcport} ${dstport} ${protocol} ${packets} ${bytes} ${start} ${end} ${action} ${log-status} ${vpc-id} ${subnet-id} ${instance-id} ${tcp-flags} ${pkt-srcaddr} ${pkt-dstaddr} ${flow-direction}' \
  --region ap-northeast-2

# 활성화된 Flow Logs 목록 확인
aws ec2 describe-flow-logs \
  --filter "Name=resource-id,Values=vpc-0123456789abcdef0" \
  --query 'FlowLogs[*].{ID:FlowLogId,Status:FlowLogStatus,Type:LogDestinationType,Dest:LogDestination,Error:DeliverLogsErrorMessage}' \
  --output table

# 상태 확인 (ACTIVE / ERROR)
aws ec2 describe-flow-logs \
  --flow-log-ids fl-0123456789abcdef0 \
  --query 'FlowLogs[0].FlowLogStatus'
```

---

## 4. S3 저장 구조 이해

### 4.1 기본 파티션 경로 (Hive 미적용)

```
s3://my-vpc-flow-logs-123456789012/
└── AWSLogs/
    └── 123456789012/               ← account-id
        └── vpcflowlogs/
            └── ap-northeast-2/     ← region
                └── 2024/
                    └── 01/
                        └── 15/
                            └── 123456789012_vpcflowlogs_ap-northeast-2_fl-xxx_20240115T1000Z_abc123.log.gz
```

파일명 패턴:
```
{account-id}_vpcflowlogs_{region}_{flow-log-id}_{YYYYMMDDTHHmmZ}_{hash}.log.gz
```

### 4.2 Hive 호환 파티션 경로 (권장)

`hive_compatible_partitions = true` 설정 시 Athena가 자동 인식하는 경로로 저장된다:

```
s3://my-vpc-flow-logs-123456789012/
└── AWSLogs/
    └── account-id=123456789012/
        └── aws-service=vpcflowlogs/
            └── aws-region=ap-northeast-2/
                └── year=2024/
                    └── month=01/
                        └── day=15/
                            └── hour=10/
                                └── 파일.log.gz
```

Hive 파티션 경로는 `MSCK REPAIR TABLE` 없이 Athena Partition Projection과 자동 연동된다.

### 4.3 파일 포맷 — plain-text vs Parquet

| 항목 | plain-text | Parquet |
|------|-----------|---------|
| 파일 크기 | 큼 (gzip 압축) | 최대 87% 작음 (컬럼 인코딩) |
| Athena 스캔량 | 전체 파일 스캔 | 컬럼 선택적 스캔 (프로젝션 푸시다운) |
| Athena 쿼리 비용 | 높음 | 낮음 (스캔량 기준 과금) |
| DDL 복잡도 | 단순 | STORED AS PARQUET, SerDe 설정 필요 |
| 설정 방법 | 기본값 | `file_format = "parquet"` 또는 Firehose 변환 |

**대용량(월 100GB+) 환경에서는 Parquet 전환이 비용 효과적이다.**

---

## 5. Athena 테이블 생성 및 파티션 전략

### 5.1 Partition Projection vs MSCK REPAIR TABLE

| 방식 | 동작 | 단점 |
|------|------|------|
| `MSCK REPAIR TABLE` | S3 경로를 스캔해 파티션 메타데이터 Glue에 등록 | S3 LIST API 호출 비용, 수동 실행 필요, 파티션 수가 많으면 수분 소요 |
| Partition Projection | Athena가 쿼리 시 파티션을 S3 경로 규칙으로 직접 계산 | Glue 카탈로그 불필요, 신규 파티션 자동 인식, 비용 없음 |

실무에서는 **Partition Projection을 기본으로 사용**한다.

### 5.2 DDL — plain-text + Partition Projection (Hive 파티션)

```sql
CREATE EXTERNAL TABLE IF NOT EXISTS vpc_flow_logs (
  version        int,
  account_id     string,
  interface_id   string,
  srcaddr        string,
  dstaddr        string,
  srcport        int,
  dstport        int,
  protocol       bigint,
  packets        bigint,
  bytes          bigint,
  start          bigint,
  end_time       bigint,   -- 'end'는 예약어라 end_time 사용
  action         string,
  log_status     string,
  vpc_id         string,
  subnet_id      string,
  instance_id    string,
  tcp_flags      int,
  pkt_srcaddr    string,
  pkt_dstaddr    string,
  flow_direction string,
  traffic_path   int
)
PARTITIONED BY (
  `account-id` string,
  `aws-service` string,
  `aws-region`  string,
  year          string,
  month         string,
  day           string,
  hour          string
)
ROW FORMAT DELIMITED
FIELDS TERMINATED BY ' '
STORED AS TEXTFILE
LOCATION 's3://my-vpc-flow-logs-123456789012/AWSLogs/'
TBLPROPERTIES (
  "skip.header.line.count" = "1",

  -- Partition Projection 설정
  "projection.enabled"              = "true",

  "projection.account-id.type"      = "enum",
  "projection.account-id.values"    = "123456789012",

  "projection.aws-service.type"     = "enum",
  "projection.aws-service.values"   = "vpcflowlogs",

  "projection.aws-region.type"      = "enum",
  "projection.aws-region.values"    = "ap-northeast-2",

  "projection.year.type"            = "integer",
  "projection.year.range"           = "2023,2030",
  "projection.year.digits"          = "4",

  "projection.month.type"           = "integer",
  "projection.month.range"          = "01,12",
  "projection.month.digits"         = "2",

  "projection.day.type"             = "integer",
  "projection.day.range"            = "01,31",
  "projection.day.digits"           = "2",

  "projection.hour.type"            = "integer",
  "projection.hour.range"           = "00,23",
  "projection.hour.digits"          = "2",

  -- S3 경로 템플릿 (Hive 파티션 경로와 일치해야 함)
  "storage.location.template" =
    "s3://my-vpc-flow-logs-123456789012/AWSLogs/account-id=${account-id}/aws-service=${aws-service}/aws-region=${aws-region}/year=${year}/month=${month}/day=${day}/hour=${hour}/"
);
```

### 5.3 DDL — Hive 미적용 (날짜 파티션만)

Hive 호환 파티션 없이 기존 방식(날짜 디렉토리)으로 저장된 경우:

```sql
CREATE EXTERNAL TABLE IF NOT EXISTS vpc_flow_logs_legacy (
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
  end_time     bigint,
  action       string,
  log_status   string,
  vpc_id       string,
  subnet_id    string,
  instance_id  string,
  tcp_flags    int
)
PARTITIONED BY (dt string)     -- dt = 'yyyy/MM/dd' 형식
ROW FORMAT DELIMITED
FIELDS TERMINATED BY ' '
STORED AS TEXTFILE
LOCATION 's3://my-vpc-flow-logs-123456789012/AWSLogs/123456789012/vpcflowlogs/ap-northeast-2/'
TBLPROPERTIES (
  "skip.header.line.count" = "1",
  "projection.enabled"     = "true",
  "projection.dt.type"     = "date",
  "projection.dt.range"    = "2023/01/01,NOW",
  "projection.dt.format"   = "yyyy/MM/dd",
  "storage.location.template" =
    "s3://my-vpc-flow-logs-123456789012/AWSLogs/123456789012/vpcflowlogs/ap-northeast-2/${dt}/"
);
```

### 5.4 DDL — Parquet 포맷

`file_format = "parquet"` 또는 Firehose 변환으로 Parquet 저장 시:

```sql
CREATE EXTERNAL TABLE IF NOT EXISTS vpc_flow_logs_parquet (
  version        int,
  account_id     string,
  interface_id   string,
  srcaddr        string,
  dstaddr        string,
  srcport        int,
  dstport        int,
  protocol       bigint,
  packets        bigint,
  bytes          bigint,
  start          bigint,
  end_time       bigint,
  action         string,
  log_status     string,
  vpc_id         string,
  subnet_id      string,
  instance_id    string,
  tcp_flags      int,
  pkt_srcaddr    string,
  pkt_dstaddr    string,
  flow_direction string
)
PARTITIONED BY (year string, month string, day string, hour string)
STORED AS PARQUET
LOCATION 's3://my-vpc-flow-logs-parquet-123456789012/flow-logs/'
TBLPROPERTIES (
  "parquet.compress"          = "SNAPPY",
  "projection.enabled"        = "true",
  "projection.year.type"      = "integer",
  "projection.year.range"     = "2023,2030",
  "projection.year.digits"    = "4",
  "projection.month.type"     = "integer",
  "projection.month.range"    = "01,12",
  "projection.month.digits"   = "2",
  "projection.day.type"       = "integer",
  "projection.day.range"      = "01,31",
  "projection.day.digits"     = "2",
  "projection.hour.type"      = "integer",
  "projection.hour.range"     = "00,23",
  "projection.hour.digits"    = "2",
  "storage.location.template" =
    "s3://my-vpc-flow-logs-parquet-123456789012/flow-logs/year=${year}/month=${month}/day=${day}/hour=${hour}/"
);
```

---

## 6. Athena 실용 쿼리 패턴

> **비용 최적화 원칙**: 모든 쿼리에 파티션 필터(year, month, day)를 반드시 포함한다. 파티션 없이 전체 스캔 시 수십~수백 GB 과금 발생 가능.

### 6.1 기본 조회 — 특정 시간대 트래픽

```sql
-- 특정 날짜, REJECT 트래픽 전체 조회
SELECT
  from_unixtime(start) AS event_time,
  srcaddr, dstaddr, srcport, dstport, protocol,
  action, tcp_flags, instance_id
FROM vpc_flow_logs
WHERE year = '2024' AND month = '01' AND day = '15'
  AND action = 'REJECT'
ORDER BY start DESC
LIMIT 100;
```

### 6.2 포트 스캔 탐지 (SYN 스캐닝)

```sql
-- 외부 IP가 다수 포트에 SYN 시도 — 포트 스캔 탐지
SELECT
  pkt_srcaddr                            AS attacker_ip,
  count(DISTINCT dstport)                AS unique_ports_scanned,
  count(*)                               AS total_attempts,
  min(from_unixtime(start))              AS first_seen,
  max(from_unixtime(start))              AS last_seen
FROM vpc_flow_logs
WHERE year = '2024' AND month = '01' AND day = '15'
  AND action = 'REJECT'
  AND tcp_flags = 2                      -- SYN only (SYN+ACK 아님)
  AND pkt_srcaddr NOT LIKE '10.%'
  AND pkt_srcaddr NOT LIKE '172.16.%'
  AND pkt_srcaddr NOT LIKE '192.168.%'
GROUP BY pkt_srcaddr
HAVING count(DISTINCT dstport) > 20     -- 20개 포트 이상 시도
ORDER BY unique_ports_scanned DESC;
```

### 6.3 데이터 유출 의심 탐지 (외부 대량 전송)

```sql
-- 외부 IP로 대용량 전송 인스턴스 (데이터 유출 의심)
SELECT
  instance_id,
  srcaddr,
  dstaddr                               AS external_ip,
  sum(bytes) / 1073741824.0             AS total_gb,
  sum(packets)                          AS total_packets,
  count(*)                              AS flow_count
FROM vpc_flow_logs
WHERE year = '2024' AND month = '01'
  AND action = 'ACCEPT'
  AND flow_direction = 'egress'
  AND instance_id != '-'
  AND dstaddr NOT LIKE '10.%'
  AND dstaddr NOT LIKE '172.16.%'
  AND dstaddr NOT LIKE '192.168.%'
  AND dstaddr NOT LIKE '169.254.%'      -- 메타데이터 서비스 제외
GROUP BY instance_id, srcaddr, dstaddr
HAVING sum(bytes) > 1073741824          -- 1GB 이상 전송
ORDER BY total_gb DESC;
```

### 6.4 TCP RST 급증 탐지 (서비스 장애 전조)

```sql
-- 특정 인스턴스로 RST 응답 급증 — 커넥션 리젝트 또는 앱 오류
SELECT
  date_trunc('hour', from_unixtime(start))  AS hour,
  dstaddr                                    AS server_ip,
  dstport,
  count(*)                                   AS rst_count
FROM vpc_flow_logs
WHERE year = '2024' AND month = '01' AND day = '15'
  AND tcp_flags & 4 > 0                      -- RST 플래그 포함
  AND action = 'ACCEPT'
GROUP BY 1, 2, 3
HAVING count(*) > 100
ORDER BY rst_count DESC;
```

### 6.5 NAT 게이트웨이 트래픽 분석

```sql
-- NAT GW ENI를 통한 인터넷 트래픽 Top 10 내부 인스턴스
-- pkt_srcaddr = 실제 프라이빗 IP, srcaddr = NAT GW IP
SELECT
  pkt_srcaddr                         AS private_ip,
  dstaddr                             AS internet_ip,
  sum(bytes) / 1048576.0              AS total_mb,
  count(*)                            AS flow_count
FROM vpc_flow_logs
WHERE year = '2024' AND month = '01' AND day = '15'
  AND interface_id IN (
    -- NAT GW의 ENI ID 목록
    'eni-nat01xxxxxxxx', 'eni-nat02xxxxxxxx'
  )
  AND flow_direction = 'egress'
  AND pkt_srcaddr != srcaddr          -- srcaddr와 다르면 NAT 변환 발생
GROUP BY pkt_srcaddr, dstaddr
ORDER BY total_mb DESC
LIMIT 10;
```

### 6.6 보안 그룹 효과 검증 — 특정 포트 접근 허용/차단 비율

```sql
-- RDS 포트(3306, 5432)에 대한 허용/차단 비율
SELECT
  dstport,
  action,
  count(*)                            AS flow_count,
  round(
    count(*) * 100.0 / sum(count(*)) OVER (PARTITION BY dstport),
    2
  )                                   AS pct
FROM vpc_flow_logs
WHERE year = '2024' AND month = '01' AND day = '15'
  AND dstport IN (3306, 5432, 1433, 6379, 27017)
GROUP BY dstport, action
ORDER BY dstport, action;
```

### 6.7 시간대별 REJECT 추이 (보안 이벤트 감지)

```sql
-- 시간별 REJECT 건수 추이 — 급증 구간 식별
SELECT
  date_format(from_unixtime(start), '%Y-%m-%d %H:00') AS hour_bucket,
  count(*)                                             AS reject_count,
  count(DISTINCT srcaddr)                              AS unique_src_ips
FROM vpc_flow_logs
WHERE year = '2024' AND month = '01' AND day = '15'
  AND action = 'REJECT'
GROUP BY date_format(from_unixtime(start), '%Y-%m-%d %H:00')
ORDER BY hour_bucket;
```

### 6.8 SKIPDATA 비율 모니터링

```sql
-- 로그 누락 비율 확인 — SKIPDATA 많으면 해당 시간대 분석 신뢰도 낮음
SELECT
  log_status,
  count(*)                             AS record_count,
  round(count(*) * 100.0 / sum(count(*)) OVER (), 2) AS pct
FROM vpc_flow_logs
WHERE year = '2024' AND month = '01' AND day = '15'
GROUP BY log_status;
```

### 6.9 특정 IP 행동 추적 (침해 사고 대응)

```sql
-- 의심 IP(x.x.x.x)의 행동 타임라인
SELECT
  from_unixtime(start)  AS ts,
  srcaddr, dstaddr,
  srcport, dstport,
  protocol,
  action,
  packets, bytes,
  tcp_flags,
  flow_direction,
  instance_id
FROM vpc_flow_logs
WHERE year = '2024' AND month = '01' AND day = '15'
  AND (srcaddr = '203.0.113.1' OR dstaddr = '203.0.113.1'
       OR pkt_srcaddr = '203.0.113.1' OR pkt_dstaddr = '203.0.113.1')
ORDER BY start ASC;
```

---

## 7. Firehose → Parquet 변환 파이프라인

대용량 환경에서 비용 최적화가 필요하면 Kinesis Data Firehose로 Flow Logs를 수신해 Parquet으로 변환한다.

```
Flow Logs → CloudWatch Logs → Subscription Filter → Firehose → S3 (Parquet)
```

```hcl
# Firehose 변환 파이프라인
resource "aws_kinesis_firehose_delivery_stream" "flow_logs_parquet" {
  name        = "vpc-flow-logs-parquet"
  destination = "extended_s3"

  extended_s3_configuration {
    role_arn           = aws_iam_role.firehose.arn
    bucket_arn         = aws_s3_bucket.flow_logs_parquet.arn
    buffering_size     = 128    # MB
    buffering_interval = 300    # 초

    prefix              = "flow-logs/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/hour=!{timestamp:HH}/"
    error_output_prefix = "flow-logs-errors/!{firehose:error-output-type}/!{timestamp:yyyy/MM/dd}/"

    data_format_conversion_configuration {
      input_format_configuration {
        deserializer {
          open_x_json_ser_de {}    # CWL JSON 형식
        }
      }

      output_format_configuration {
        serializer {
          parquet_ser_de {
            compression = "SNAPPY"
          }
        }
      }

      schema_configuration {
        database_name = "default"
        table_name    = "vpc_flow_logs_schema"    # Glue 테이블 스키마 참조
        role_arn      = aws_iam_role.firehose.arn
      }
    }
  }
}

# CWL → Firehose 구독 필터
resource "aws_cloudwatch_log_subscription_filter" "to_firehose" {
  name            = "vpc-flow-logs-to-firehose"
  log_group_name  = aws_cloudwatch_log_group.flow_logs_reject.name
  filter_pattern  = ""    # 전체 전달
  destination_arn = aws_kinesis_firehose_delivery_stream.flow_logs_parquet.arn
  role_arn        = aws_iam_role.cwl_to_firehose.arn
}
```

---

## 8. 모니터링 및 알람

### 8.1 CloudWatch Logs Metric Filter — REJECT 급증 알람

```hcl
resource "aws_cloudwatch_log_metric_filter" "reject_count" {
  name           = "vpc-reject-traffic"
  log_group_name = aws_cloudwatch_log_group.flow_logs_reject.name

  # v2 기본 포맷 필드 순서와 일치해야 함
  pattern = "[version, account_id, interface_id, srcaddr, dstaddr, srcport, dstport, protocol, packets, bytes, start, end, action=REJECT, ...]"

  metric_transformation {
    name          = "RejectCount"
    namespace     = "Custom/VPCFlowLogs"
    value         = "1"
    default_value = 0
  }
}

resource "aws_cloudwatch_metric_alarm" "reject_spike" {
  alarm_name          = "vpc-reject-spike"
  alarm_description   = "REJECT 트래픽 5분 내 1000건 초과 — 포트 스캔 또는 공격 의심"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "RejectCount"
  namespace           = "Custom/VPCFlowLogs"
  period              = 300
  statistic           = "Sum"
  threshold           = 1000
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.security_alerts.arn]
}
```

### 8.2 CloudWatch Logs Insights — 실시간 조회

```
# 최근 1시간 REJECT 상위 소스 IP
fields srcaddr, dstport, action
| filter action = "REJECT"
| stats count(*) as cnt by srcaddr, dstport
| sort cnt desc
| limit 20
```

```
# TCP RST 급증 탐지
fields @timestamp, srcaddr, dstaddr, dstport, tcp_flags
| filter tcp_flags >= 4                   # RST 비트 포함
| stats count(*) as rst_count by bin(5m)
| sort rst_count desc
```

---

## 9. 트러블슈팅

### 9.1 Flow Logs가 S3에 쌓이지 않음

```bash
# 에러 메시지 확인
aws ec2 describe-flow-logs \
  --filter "Name=resource-id,Values=vpc-0abc123" \
  --query 'FlowLogs[*].{Status:FlowLogStatus,Error:DeliverLogsErrorMessage}'

# 흔한 원인 및 해결
# 1. S3 버킷 정책 누락 → delivery.logs.amazonaws.com Principal에 s3:PutObject 허용 확인
# 2. 크로스 계정 버킷 → 버킷 정책 aws:SourceAccount Condition 확인
# 3. S3 Object Lock 활성화 → Flow Logs 전달 불가, Object Lock 비활성화 필요
```

### 9.2 Athena 쿼리 결과가 없거나 0건

```bash
# S3 실제 파일 존재 여부 확인
aws s3 ls s3://my-vpc-flow-logs-123456789012/AWSLogs/ --recursive | head -20

# 파티션 경로와 테이블 LOCATION이 일치하는지 확인
# storage.location.template의 변수명이 PARTITIONED BY 컬럼명과 일치해야 함
```

```sql
-- Athena에서 파티션 확인
SHOW PARTITIONS vpc_flow_logs;

-- Projection 미사용 테이블이면 수동 파티션 등록
MSCK REPAIR TABLE vpc_flow_logs;
```

### 9.3 `end` 필드가 Athena에서 오류

`end`는 Athena/Presto에서 예약어다. DDL에서 `end_time bigint`로 정의하거나 쿼리 시 백틱으로 감싸야 한다:

```sql
SELECT `end` FROM vpc_flow_logs ...;  -- 백틱으로 예약어 이스케이프
-- 또는 DDL에서 end_time으로 정의 (권장)
```

### 9.4 SKIPDATA 비율이 높음

원인: 단일 ENI에 트래픽이 집중되어 내부 버퍼 초과

대응:
- 분석 목적이면 샘플링 허용 (완벽한 해결책 없음)
- ENI 단위 Flow Logs 대신 서브넷 또는 VPC 단위로 올려 분산 수집 시도
- `max_aggregation_interval = 60`으로 낮춰 버퍼 압박 감소 시도

### 9.5 Athena 쿼리 비용이 과도하게 높음

```sql
-- 나쁜 예: 파티션 필터 없음 → 전체 S3 스캔
SELECT * FROM vpc_flow_logs WHERE action = 'REJECT' LIMIT 100;

-- 좋은 예: 파티션 필터 포함
SELECT * FROM vpc_flow_logs
WHERE year = '2024' AND month = '01' AND day = '15'
  AND action = 'REJECT'
LIMIT 100;
```

비용 추가 절감:
1. Parquet 포맷 전환 (텍스트 대비 스캔량 80~90% 감소)
2. `per_hour_files = true` 설정으로 파일 단위 파티션 세분화
3. 자주 쓰는 쿼리는 Athena 결과 재사용(Query Result Reuse) 활용

---

## 10. 아키텍처 선택 가이드

| 규모 | 목적 | 권장 구성 |
|------|------|----------|
| 소규모 (< 10GB/월) | 보안 감사, 가끔 조회 | Flow Logs → S3 plain-text → Athena |
| 중규모 (10~100GB/월) | 정기 분석 + 실시간 알람 | S3 (ALL) + CloudWatch Logs (REJECT only) |
| 대규모 (> 100GB/월) | 비용 최적화 필수 | Flow Logs → S3 Parquet (직접) 또는 CWL → Firehose → Parquet |
| 멀티 계정 | 중앙 집중 분석 | Log Archive 계정의 중앙 S3 버킷으로 크로스 계정 전달, Organizations Trail 패턴 적용 |

---

## 11. TIP

- **GuardDuty + Flow Logs 연동**: GuardDuty는 Flow Logs를 자동으로 수집·분석한다. Flow Logs를 직접 켜지 않아도 GuardDuty 활성화만으로 분석 가능하나, 장기 보관 및 커스텀 쿼리에는 직접 S3 저장이 필요
- **VPC Reachability Analyzer**: 통신 불가 문제 사전 진단. Flow Logs의 사후 분석과 달리 경로 차단 지점을 시뮬레이션으로 미리 파악
- **Partition Projection 범위 주의**: `projection.year.range = "2023,2030"` 범위 밖 데이터는 쿼리 결과에서 제외됨. 오래된 데이터 조회 시 범위 수정 필요
- **flow-direction 활용**: ingress/egress를 구분하면 NAT GW 비용 분석(외부 트래픽 인스턴스별 집계) 및 내부 East-West 트래픽 감사에 효과적
