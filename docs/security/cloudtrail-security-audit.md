# CloudTrail 기반 보안 감사 자동화

## 1. 개요

CloudTrail은 AWS 계정의 모든 API 호출을 기록하는 감사 서비스다.
누가, 언제, 어디서, 무엇을 했는지 추적하며 보안 사고 조사, 규정 준수, 이상 탐지에 활용된다.
EventBridge와 연동해 주요 이벤트 발생 시 실시간 알람을 받을 수 있다.

---

## 2. 설명

### 2.1 핵심 개념

**이벤트 유형 3가지**

| 유형 | 설명 | 비용 |
|------|------|------|
| Management Events | API 호출 (Create/Delete/Modify) | 첫 복사본 무료 |
| Data Events | S3 Object 접근, Lambda 호출 등 | $0.10/100,000 이벤트 |
| Insight Events | 비정상적 API 호출 패턴 감지 | $0.35/100,000 이벤트 |

**이벤트 로그 주요 필드**

```json
{
  "eventTime": "2024-01-15T10:30:00Z",
  "eventName": "StopInstances",
  "eventSource": "ec2.amazonaws.com",
  "userIdentity": {
    "type": "IAMUser",
    "userName": "john.doe",
    "arn": "arn:aws:iam::123456789012:user/john.doe",
    "accountId": "123456789012"
  },
  "sourceIPAddress": "203.0.113.10",
  "userAgent": "aws-cli/2.x",
  "requestParameters": {
    "instancesSet": {"items": [{"instanceId": "i-xxxxxxxx"}]}
  },
  "responseElements": { ... },
  "errorCode": null,   // 오류 시 "AccessDenied" 등
  "errorMessage": null
}
```

---

### 2.2 실무 적용 코드

**Terraform — Trail 생성 (멀티 리전, KMS 암호화)**

```hcl
# CloudTrail 로그 저장 S3 버킷
resource "aws_s3_bucket" "cloudtrail" {
  bucket = "my-cloudtrail-logs-${var.account_id}"
}

resource "aws_s3_bucket_policy" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AWSCloudTrailAclCheck"
        Effect = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "s3:GetBucketAcl"
        Resource  = aws_s3_bucket.cloudtrail.arn
      },
      {
        Sid    = "AWSCloudTrailWrite"
        Effect = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.cloudtrail.arn}/AWSLogs/${var.account_id}/*"
        Condition = {
          StringEquals = {
            "s3:x-amz-acl" = "bucket-owner-full-control"
          }
        }
      }
    ]
  })
}

# CloudWatch Logs 연동
resource "aws_cloudwatch_log_group" "cloudtrail" {
  name              = "/aws/cloudtrail"
  retention_in_days = 90   # 90일 보관
}

resource "aws_iam_role" "cloudtrail" {
  name = "cloudtrail-cloudwatch-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "cloudtrail.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "cloudtrail" {
  role = aws_iam_role.cloudtrail.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource = "${aws_cloudwatch_log_group.cloudtrail.arn}:*"
    }]
  })
}

# Trail 생성 (멀티 리전, 글로벌 서비스 포함)
resource "aws_cloudtrail" "main" {
  name                          = "main-trail"
  s3_bucket_name                = aws_s3_bucket.cloudtrail.id
  include_global_service_events = true   # IAM, STS 등 글로벌 서비스 포함
  is_multi_region_trail         = true   # 모든 리전 이벤트 수집
  enable_log_file_validation    = true   # 로그 무결성 검증

  cloud_watch_logs_group_arn = "${aws_cloudwatch_log_group.cloudtrail.arn}:*"
  cloud_watch_logs_role_arn  = aws_iam_role.cloudtrail.arn

  kms_key_id = aws_kms_key.cloudtrail.arn

  # S3 Data Events 활성화 (선택적, 비용 발생)
  event_selector {
    read_write_type           = "All"
    include_management_events = true

    data_resource {
      type   = "AWS::S3::Object"
      values = ["arn:aws:s3:::sensitive-bucket/"]   # 특정 버킷만
    }
  }
}
```

**CloudWatch Metric Filter + Alarm — 주요 보안 이벤트**

```hcl
locals {
  security_alarms = {
    root_login = {
      pattern = "{ $.userIdentity.type = \"Root\" && $.eventType = \"AwsConsoleSignIn\" }"
      message = "Root 계정 콘솔 로그인 감지"
    }
    no_mfa_console_login = {
      pattern = "{ $.eventName = \"ConsoleLogin\" && $.additionalEventData.MFAUsed != \"Yes\" }"
      message = "MFA 없는 콘솔 로그인 감지"
    }
    security_group_change = {
      pattern = "{ $.eventName = \"AuthorizeSecurityGroupIngress\" || $.eventName = \"RevokeSecurityGroupIngress\" || $.eventName = \"CreateSecurityGroup\" || $.eventName = \"DeleteSecurityGroup\" }"
      message = "보안그룹 변경 감지"
    }
    iam_policy_change = {
      pattern = "{ $.eventName = \"CreatePolicy\" || $.eventName = \"DeletePolicy\" || $.eventName = \"AttachRolePolicy\" || $.eventName = \"DetachRolePolicy\" }"
      message = "IAM 정책 변경 감지"
    }
    s3_bucket_policy_change = {
      pattern = "{ $.eventName = \"PutBucketPolicy\" || $.eventName = \"DeleteBucketPolicy\" || $.eventName = \"PutBucketAcl\" }"
      message = "S3 버킷 정책/ACL 변경 감지"
    }
    cloudtrail_stopped = {
      pattern = "{ $.eventName = \"StopLogging\" || $.eventName = \"DeleteTrail\" }"
      message = "CloudTrail 로깅 중단/삭제 감지 — 즉시 조사 필요"
    }
    unauthorized_api_call = {
      pattern = "{ $.errorCode = \"AccessDenied\" || $.errorCode = \"UnauthorizedOperation\" }"
      message = "권한 없는 API 호출 시도"
    }
  }
}

resource "aws_cloudwatch_log_metric_filter" "security" {
  for_each = local.security_alarms

  name           = each.key
  pattern        = each.value.pattern
  log_group_name = aws_cloudwatch_log_group.cloudtrail.name

  metric_transformation {
    name      = each.key
    namespace = "CloudTrailSecurityMetrics"
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "security" {
  for_each = local.security_alarms

  alarm_name          = "security-${each.key}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = each.key
  namespace           = "CloudTrailSecurityMetrics"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_description   = each.value.message
  alarm_actions       = [aws_sns_topic.security_alerts.arn]
}
```

**Athena — CloudTrail 로그 분석**

```sql
-- CloudTrail Athena 테이블 생성
CREATE EXTERNAL TABLE cloudtrail_logs (
  eventVersion      string,
  userIdentity      struct<
    type:string,
    principalId:string,
    arn:string,
    accountId:string,
    userName:string
  >,
  eventTime         string,
  eventSource       string,
  eventName         string,
  awsRegion         string,
  sourceIPAddress   string,
  userAgent         string,
  errorCode         string,
  errorMessage      string,
  requestParameters string,
  responseElements  string,
  requestId         string,
  eventId           string,
  resources         array<struct<ARN:string,accountId:string,type:string>>,
  eventType         string,
  recipientAccountId string
)
PARTITIONED BY (region string, year string, month string, day string)
ROW FORMAT SERDE 'org.apache.hive.hcatalog.data.JsonSerDe'
STORED AS INPUTFORMAT 'com.amazon.emr.cloudtrail.CloudTrailInputFormat'
OUTPUTFORMAT 'org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat'
LOCATION 's3://my-cloudtrail-logs-123456789012/AWSLogs/123456789012/CloudTrail/'
TBLPROPERTIES (
  "projection.enabled"="true",
  "projection.region.type"="enum",
  "projection.region.values"="ap-northeast-2,us-east-1",
  "projection.year.type"="integer",
  "projection.year.range"="2024,2030",
  "projection.month.type"="integer",
  "projection.month.range"="1,12",
  "projection.month.digits"="2",
  "projection.day.type"="integer",
  "projection.day.range"="1,31",
  "projection.day.digits"="2",
  "storage.location.template"="s3://my-cloudtrail-logs-123456789012/AWSLogs/123456789012/CloudTrail/${region}/${year}/${month}/${day}"
);

-- 1. AccessDenied 상위 발생자
SELECT userIdentity.userName, userIdentity.arn,
       eventName, errorCode, count(*) AS cnt
FROM cloudtrail_logs
WHERE errorCode IN ('AccessDenied', 'UnauthorizedOperation')
  AND year = '2024' AND month = '01'
GROUP BY userIdentity.userName, userIdentity.arn, eventName, errorCode
ORDER BY cnt DESC
LIMIT 20;

-- 2. 특정 S3 버킷의 비정상 접근
SELECT eventTime, userIdentity.userName, sourceIPAddress,
       eventName, requestParameters
FROM cloudtrail_logs
WHERE eventSource = 's3.amazonaws.com'
  AND json_extract_scalar(requestParameters, '$.bucketName') = 'my-sensitive-bucket'
  AND year = '2024' AND month = '01'
ORDER BY eventTime DESC;

-- 3. IAM 권한 변경 이력
SELECT eventTime, userIdentity.userName, eventName,
       json_extract_scalar(requestParameters, '$.roleName') AS role_name,
       json_extract_scalar(requestParameters, '$.policyArn') AS policy_arn
FROM cloudtrail_logs
WHERE eventSource = 'iam.amazonaws.com'
  AND eventName IN ('AttachRolePolicy', 'DetachRolePolicy', 'PutRolePolicy',
                    'CreateRole', 'DeleteRole')
  AND year = '2024' AND month = '01'
ORDER BY eventTime DESC;

-- 4. 비업무 시간 활동 (오전 9시 이전 또는 오후 6시 이후)
SELECT eventTime, userIdentity.userName, eventName, awsRegion
FROM cloudtrail_logs
WHERE userIdentity.type = 'IAMUser'
  AND (CAST(SUBSTRING(eventTime, 12, 2) AS int) < 9
       OR CAST(SUBSTRING(eventTime, 12, 2) AS int) >= 18)
  AND year = '2024' AND month = '01'
ORDER BY eventTime DESC;
```

---

### 2.3 보안/비용 Best Practice

- **멀티 리전 Trail 1개**: 리전별 개별 Trail보다 비용 효율적
- **S3 버킷은 별도 보안 계정에**: Trail 로그를 운영 계정이 아닌 보안 전용 계정 S3에 저장 → 침해 시 로그 은폐 방지
- **로그 무결성 검증 활성화**: `enable_log_file_validation = true`로 로그 위변조 탐지
- **Data Events는 필요한 버킷만**: 전체 S3 Data Events는 비용 폭증 가능

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**이벤트 누락 — 리전 누락**

```bash
# Trail이 멀티 리전인지 확인
aws cloudtrail describe-trails \
  --query 'trailList[*].{Name:Name,MultiRegion:IsMultiRegionTrail}'

# 특정 리전에 Trail이 없는지 확인
aws cloudtrail get-trail-status \
  --name my-trail \
  --region us-east-1   # 모든 리전에서 확인
```

**CloudWatch Logs에 이벤트가 안 보임**

```bash
# Trail의 CloudWatch Logs 연동 상태
aws cloudtrail describe-trails \
  --query 'trailList[*].{CloudWatchLogsGroupArn:CloudWatchLogsLogGroupArn,RoleArn:CloudWatchLogsRoleArn}'

# IAM Role 권한 확인
aws iam simulate-principal-policy \
  --policy-source-arn <cloudtrail-role-arn> \
  --action-names "logs:PutLogEvents" \
  --resource-arns <log-group-arn>
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: CloudTrail 이벤트와 실제 발생 시간 사이에 지연이 있나요?**
A: 일반적으로 15분 이내에 S3에 전달됩니다. CloudWatch Logs는 더 빠릅니다(수분 이내). 하지만 보장된 SLA는 없습니다.

**Q: root 계정 활동은 CloudTrail에 기록되나요?**
A: 예. root 계정의 모든 API 호출도 기록됩니다. `userIdentity.type == "Root"` 필터로 조회 가능합니다.

---

## 4. 모니터링 및 알람

**EventBridge — 실시간 보안 이벤트 반응**

```hcl
# GuardDuty 발견 사항을 Slack으로 전송
resource "aws_cloudwatch_event_rule" "guardduty_finding" {
  name = "guardduty-high-severity"

  event_pattern = jsonencode({
    source      = ["aws.guardduty"]
    detail-type = ["GuardDuty Finding"]
    detail = {
      severity = [{ numeric = [">=", 7.0] }]   # HIGH 이상
    }
  })
}
```

---

## 5. TIP

- **CloudTrail Lake**: S3 + Athena 대신 CloudTrail Lake를 사용하면 7년간 이벤트를 SQL로 직접 조회 가능 (비용 별도)
- **AWS Config + CloudTrail 조합**: Config로 리소스 상태 변경을 추적하고, CloudTrail로 누가 변경했는지 연계 조회
- **보안 대응 자동화**: CloudTrail → EventBridge → Lambda → 자동 조치 (예: 비정상 IAM 키 자동 비활성화)
