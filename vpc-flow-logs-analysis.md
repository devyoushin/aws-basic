# VPC Flow Logs 분석 & 보안 감사

## 1. 개요

VPC Flow Logs는 VPC의 ENI (Elastic Network Interface)를 통과하는 IP 트래픽을 기록하는 기능이다.
ACCEPT/REJECT 구분, 소스/목적지 IP:포트를 기록하여 보안 감사, 트래픽 분석, 이상 탐지에 활용된다.
CloudWatch Logs 또는 S3에 저장하며, Athena로 SQL 쿼리하는 패턴이 가장 실용적이다.

---

## 2. 설명

### 2.1 핵심 개념

**Flow Logs 레코드 필드**

| 필드 | 설명 | 예시 |
|------|------|------|
| `version` | 레코드 버전 | 2 |
| `account-id` | AWS 계정 ID | 123456789012 |
| `interface-id` | ENI ID | eni-xxxxxxxx |
| `srcaddr` | 소스 IP | 10.0.1.100 |
| `dstaddr` | 목적지 IP | 10.0.2.200 |
| `srcport` | 소스 포트 | 49152 |
| `dstport` | 목적지 포트 | 443 |
| `protocol` | 프로토콜 (6=TCP, 17=UDP, 1=ICMP) | 6 |
| `packets` | 패킷 수 | 15 |
| `bytes` | 바이트 수 | 1500 |
| `start` / `end` | 집계 시작/종료 시간 (Unix timestamp) | 1609459200 |
| `action` | ACCEPT 또는 REJECT | REJECT |
| `log-status` | OK / NODATA / SKIPDATA | OK |

**log-status 의미**

| 값 | 의미 |
|----|------|
| `OK` | 정상 기록 |
| `NODATA` | 집계 기간 중 트래픽 없음 |
| `SKIPDATA` | 내부 용량 제약으로 일부 레코드 누락 |

**저장 위치별 비교**

| 항목 | CloudWatch Logs | S3 |
|------|----------------|-----|
| 비용 | $0.76/GB (수집) + 보관 | $0.023/GB (보관) + PUT 요청 |
| 실시간성 | 수분 이내 | 5~15분 딜레이 |
| 쿼리 | CloudWatch Logs Insights | Athena (S3 Select) |
| 보관 기간 | 설정 가능 | S3 Lifecycle 정책 |
| 권장 용도 | 실시간 알람 | 장기 보관, 대용량 분석 |

---

### 2.2 실무 적용 코드

**Terraform — Flow Logs 활성화 (S3 + CloudWatch Logs)**

```hcl
# S3 버킷 생성
resource "aws_s3_bucket" "flow_logs" {
  bucket = "my-vpc-flow-logs-${var.account_id}"
}

resource "aws_s3_bucket_lifecycle_configuration" "flow_logs" {
  bucket = aws_s3_bucket.flow_logs.id

  rule {
    id     = "expire-old-logs"
    status = "Enabled"

    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }

    expiration {
      days = 365    # 1년 후 삭제
    }
  }
}

# Flow Logs IAM Role
resource "aws_iam_role" "flow_logs" {
  name = "vpc-flow-logs-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "vpc-flow-logs.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "flow_logs" {
  role = aws_iam_role.flow_logs.id
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

# VPC Flow Logs — S3로 저장
resource "aws_flow_log" "s3" {
  vpc_id               = aws_vpc.main.id
  traffic_type         = "ALL"    # ACCEPT, REJECT, ALL
  log_destination_type = "s3"
  log_destination      = aws_s3_bucket.flow_logs.arn

  # 커스텀 필드 (v3 이상)
  log_format = "$${version} $${account-id} $${interface-id} $${srcaddr} $${dstaddr} $${srcport} $${dstport} $${protocol} $${packets} $${bytes} $${start} $${end} $${action} $${log-status} $${vpc-id} $${subnet-id} $${instance-id} $${tcp-flags}"
}

# VPC Flow Logs — CloudWatch Logs로 저장 (실시간 알람용)
resource "aws_cloudwatch_log_group" "flow_logs" {
  name              = "/aws/vpc/flow-logs"
  retention_in_days = 30
}

resource "aws_flow_log" "cloudwatch" {
  vpc_id          = aws_vpc.main.id
  traffic_type    = "REJECT"   # REJECT만 저장 (비용 절감)
  iam_role_arn    = aws_iam_role.flow_logs.arn
  log_destination = aws_cloudwatch_log_group.flow_logs.arn
}
```

**Athena — Flow Logs 분석 테이블 생성 (DDL)**

```sql
CREATE EXTERNAL TABLE vpc_flow_logs (
  version     int,
  account_id  string,
  interface_id string,
  srcaddr     string,
  dstaddr     string,
  srcport     int,
  dstport     int,
  protocol    bigint,
  packets     bigint,
  bytes       bigint,
  start       bigint,
  end         bigint,
  action      string,
  log_status  string,
  vpc_id      string,
  subnet_id   string,
  instance_id string,
  tcp_flags   int
)
PARTITIONED BY (partition_date string)
ROW FORMAT DELIMITED
FIELDS TERMINATED BY ' '
STORED AS TEXTFILE
LOCATION 's3://my-vpc-flow-logs-123456789012/AWSLogs/123456789012/vpcflowlogs/ap-northeast-2/'
TBLPROPERTIES (
  "skip.header.line.count"="1",
  "projection.enabled"="true",
  "projection.partition_date.type"="date",
  "projection.partition_date.range"="2024/01/01,NOW",
  "projection.partition_date.format"="yyyy/MM/dd",
  "storage.location.template"="s3://my-vpc-flow-logs-123456789012/AWSLogs/123456789012/vpcflowlogs/ap-northeast-2/${partition_date}"
);
```

**Athena — 보안 감사 쿼리 예시**

```sql
-- 1. 특정 IP에서 거부된 트래픽 상위 10개
SELECT srcaddr, dstaddr, dstport, count(*) AS cnt
FROM vpc_flow_logs
WHERE action = 'REJECT'
  AND partition_date >= '2024/01/01'
  AND srcaddr NOT LIKE '10.%'   -- 외부 IP만
GROUP BY srcaddr, dstaddr, dstport
ORDER BY cnt DESC
LIMIT 10;

-- 2. 비정상 포트 접근 시도 (스캐닝 탐지)
SELECT srcaddr, count(DISTINCT dstport) AS port_count, count(*) AS total_attempts
FROM vpc_flow_logs
WHERE action = 'REJECT'
  AND partition_date = '2024/01/15'
  AND srcaddr NOT LIKE '10.%'
GROUP BY srcaddr
HAVING count(DISTINCT dstport) > 10   -- 10개 이상 포트 시도
ORDER BY port_count DESC;

-- 3. 대용량 트래픽 발신 인스턴스 (데이터 유출 의심)
SELECT instance_id, srcaddr,
       sum(bytes)/1073741824.0 AS total_gb
FROM vpc_flow_logs
WHERE action = 'ACCEPT'
  AND partition_date >= '2024/01/01'
  AND dstaddr NOT LIKE '10.%'    -- 외부로 나가는 트래픽
  AND instance_id != '-'
GROUP BY instance_id, srcaddr
ORDER BY total_gb DESC
LIMIT 20;

-- 4. RDS 포트(3306, 5432)에 허용되지 않은 접근
SELECT srcaddr, dstaddr, dstport, action, count(*) AS cnt
FROM vpc_flow_logs
WHERE dstport IN (3306, 5432, 1433, 27017)
  AND partition_date >= '2024/01/01'
GROUP BY srcaddr, dstaddr, dstport, action
ORDER BY cnt DESC;

-- 5. 특정 시간대 REJECT 급증 (보안 이벤트 탐지)
SELECT date_format(from_unixtime(start), '%Y-%m-%d %H:00') AS hour,
       count(*) AS reject_count
FROM vpc_flow_logs
WHERE action = 'REJECT'
  AND partition_date >= '2024/01/01'
GROUP BY date_format(from_unixtime(start), '%Y-%m-%d %H:00')
ORDER BY reject_count DESC
LIMIT 24;
```

---

### 2.3 보안/비용 Best Practice

- **REJECT 트래픽만 CloudWatch에, ALL 트래픽은 S3에**: 비용 최적화
- **S3 버킷 정책으로 Flow Logs 덮어쓰기 방지**: `s3:PutObjectAcl` 권한 제거
- **Partition Projection 활성화**: Athena에서 날짜별 파티션 자동 인식 → MSCK REPAIR 불필요, 쿼리 비용 절감
- **Flow Logs 압축**: Parquet 형식으로 저장 시 Athena 쿼리 비용 최대 85% 절감 (Amazon Data Firehose 활용)

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**Flow Logs가 S3에 수집되지 않음**

```bash
# Flow Logs 상태 확인
aws ec2 describe-flow-logs \
  --filter "Name=resource-id,Values=vpc-xxxxxxxx" \
  --query 'FlowLogs[*].{ID:FlowLogId,Status:FlowLogStatus,Destination:LogDestination}'

# 오류 확인
aws ec2 describe-flow-logs \
  --filter "Name=resource-id,Values=vpc-xxxxxxxx" \
  --query 'FlowLogs[*].DeliverLogsErrorMessage'

# 흔한 원인: S3 버킷 정책에 flow-logs 서비스 Principal 누락
```

**SKIPDATA 레코드 발생**

```bash
# Athena로 SKIPDATA 비율 확인
SELECT log_status, count(*) AS cnt
FROM vpc_flow_logs
WHERE partition_date = '2024/01/15'
GROUP BY log_status;

# SKIPDATA 원인: 트래픽 급증으로 내부 버퍼 초과
# 해결: 서브넷 단위가 아닌 ENI 단위로 세분화하거나 집계 윈도우 단축 불가 (AWS 내부 제한)
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: Flow Logs에서 내 EC2 인스턴스의 IP가 아닌 다른 IP가 많이 보입니다**
A: NLB/ALB는 별도의 ENI를 가지며, 이 ENI의 트래픽도 Flow Logs에 기록됩니다. `interface_id`로 필터링해 특정 ENI의 트래픽만 분석하세요.

**Q: Flow Logs 비용이 너무 높습니다**
A: 트래픽 규모에 따라 비용이 선형 증가합니다. 방법: 1) `traffic_type = "REJECT"`만 수집, 2) S3 저장 + Athena 쿼리 (CloudWatch Logs보다 저렴), 3) 중요 서브넷만 선택적 활성화.

---

## 4. 모니터링 및 알람

```hcl
# CloudWatch Logs에서 REJECT 급증 감지
resource "aws_cloudwatch_log_metric_filter" "reject_traffic" {
  name           = "vpc-reject-traffic"
  pattern        = "[version, account_id, interface_id, srcaddr, dstaddr, srcport, dstport, protocol, packets, bytes, start, end, action=REJECT, log_status]"
  log_group_name = "/aws/vpc/flow-logs"

  metric_transformation {
    name      = "RejectCount"
    namespace = "Custom/VPC"
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "reject_high" {
  alarm_name          = "vpc-reject-traffic-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "RejectCount"
  namespace           = "Custom/VPC"
  period              = 300
  statistic           = "Sum"
  threshold           = 1000   # 5분 내 거부 1000건 이상
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
```

---

## 5. TIP

- **VPC Reachability Analyzer**: Flow Logs가 아닌 경로 분석 도구 — 특정 경로가 통신 가능한지 사전 검증 가능
- **AWS Security Hub**: Flow Logs를 GuardDuty와 연동하면 위협 인텔리전스 기반 이상 탐지 자동화
- **Athena 쿼리 비용 절감**: `LIMIT`와 날짜 파티션 필터를 항상 포함해 스캔 데이터 최소화
