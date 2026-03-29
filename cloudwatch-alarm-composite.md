# CloudWatch Composite Alarm

## 1. 개요
- Composite Alarm은 여러 개의 CloudWatch 알람을 AND/OR 논리 연산자로 조합하여 하나의 알람으로 만드는 기능
- 단일 지표 알람의 오탐(False Positive)을 줄이고, 연관 증상이 동시에 발생할 때만 알림을 보내 Alarm Storm을 방지
- 운영 중 특정 장애 상황(예: CPU + 메모리 + 요청 오류율 동시 증가)을 정밀하게 감지할 수 있어 On-call 부담 감소

## 2. 설명
### 2.1 핵심 개념
- **단순 알람(Metric Alarm)** vs **복합 알람(Composite Alarm)** 비교

| 항목 | Metric Alarm | Composite Alarm |
|------|-------------|----------------|
| 기반 | 단일 CloudWatch 지표 | 여러 Metric/Composite 알람 조합 |
| 액션 | SNS, Auto Scaling, EC2 작업 | SNS 알림만 가능 |
| 상태 | OK / ALARM / INSUFFICIENT_DATA | OK / ALARM |
| 요금 | 알람당 과금 | 별도 추가 과금 없음 |

- **Rule Expression** 문법:
  - `ALARM("알람명")` — 해당 알람이 ALARM 상태인지 확인
  - `OK("알람명")` — 해당 알람이 OK 상태인지 확인
  - `AND`, `OR`, `NOT` 논리 연산자 사용
  - 중첩 괄호로 복잡한 조건 구성 가능

- **Alarm Storm** 패턴: 장애 시 수십 개 알람이 동시에 울리는 현상 → Composite Alarm으로 최상위 알람 1개만 알림

### 2.2 실무 적용 코드

**Terraform — 기본 Composite Alarm**
```hcl
# 개별 Metric Alarm
resource "aws_cloudwatch_metric_alarm" "cpu_high" {
  alarm_name          = "prod-api-cpu-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "CPUUtilization"
  namespace           = "AWS/EC2"
  period              = 60
  statistic           = "Average"
  threshold           = 80
  dimensions = {
    AutoScalingGroupName = "prod-api-asg"
  }
}

resource "aws_cloudwatch_metric_alarm" "error_rate_high" {
  alarm_name          = "prod-api-error-rate-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "5XXError"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Sum"
  threshold           = 50
  dimensions = {
    LoadBalancer = aws_lb.prod.arn_suffix
  }
}

resource "aws_cloudwatch_metric_alarm" "latency_high" {
  alarm_name          = "prod-api-latency-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "TargetResponseTime"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  extended_statistic  = "p99"
  threshold           = 2
  dimensions = {
    LoadBalancer = aws_lb.prod.arn_suffix
  }
}

# Composite Alarm — CPU 높거나 (에러율 높고 레이턴시 높을 때)
resource "aws_cloudwatch_composite_alarm" "prod_api_critical" {
  alarm_name        = "prod-api-critical"
  alarm_description = "프로덕션 API 서비스 장애 감지 (오탐 방지 복합 알람)"

  alarm_rule = <<-EOT
    ALARM("prod-api-cpu-high") OR
    (ALARM("prod-api-error-rate-high") AND ALARM("prod-api-latency-high"))
  EOT

  alarm_actions = [aws_sns_topic.oncall.arn]
  ok_actions    = [aws_sns_topic.oncall.arn]

  depends_on = [
    aws_cloudwatch_metric_alarm.cpu_high,
    aws_cloudwatch_metric_alarm.error_rate_high,
    aws_cloudwatch_metric_alarm.latency_high,
  ]
}
```

**AWS CLI — Composite Alarm 생성**
```bash
aws cloudwatch put-composite-alarm \
  --alarm-name "prod-api-critical" \
  --alarm-rule 'ALARM("prod-api-cpu-high") OR (ALARM("prod-api-error-rate-high") AND ALARM("prod-api-latency-high"))' \
  --alarm-actions "arn:aws:sns:ap-northeast-2:123456789012:oncall-topic" \
  --ok-actions "arn:aws:sns:ap-northeast-2:123456789012:oncall-topic"

# 현재 상태 확인
aws cloudwatch describe-alarms \
  --alarm-names "prod-api-critical" \
  --alarm-types CompositeAlarm
```

**Alarm Storm 방지 패턴 — 계층형 Composite Alarm**
```hcl
# 1단계: 서비스별 복합 알람
resource "aws_cloudwatch_composite_alarm" "api_service" {
  alarm_name = "api-service-composite"
  alarm_rule = <<-EOT
    ALARM("api-cpu-high") AND ALARM("api-error-rate-high")
  EOT
}

resource "aws_cloudwatch_composite_alarm" "db_service" {
  alarm_name = "db-service-composite"
  alarm_rule = <<-EOT
    ALARM("rds-cpu-high") OR ALARM("rds-connection-high")
  EOT
}

# 2단계: 전체 시스템 알람 (1단계 복합 알람 조합)
resource "aws_cloudwatch_composite_alarm" "system_critical" {
  alarm_name = "system-critical"
  alarm_rule = <<-EOT
    ALARM("api-service-composite") OR ALARM("db-service-composite")
  EOT
  alarm_actions = [aws_sns_topic.pagerduty.arn]
}
```

### 2.3 보안/비용 Best Practice
- SNS 토픽에 암호화(KMS) 적용 — 알람 메시지에 민감 정보가 포함될 수 있음
- Composite Alarm은 하위 알람이 최소 1개 이상 ALARM 상태여야 전환 — INSUFFICIENT_DATA가 많으면 오탐 방지 효과가 없음
- On-call 팀 SNS 구독과 일반 알림 SNS 구독을 분리해 알람 등급 구분
- 비용: Composite Alarm 자체는 추가 비용 없음. 단, 하위 Metric Alarm 개수만큼 과금

## 3. 트러블슈팅
### 3.1 주요 이슈

**Composite Alarm이 ALARM으로 전환되지 않는 경우**
- 증상: 하위 알람이 ALARM인데 복합 알람은 OK 유지
- 원인: Rule Expression 오타 또는 알람 이름 불일치
- 해결:
  ```bash
  # 알람 이름 정확히 확인
  aws cloudwatch describe-alarms --alarm-name-prefix "prod-api"

  # Rule Expression 검증
  aws cloudwatch describe-alarms \
    --alarm-names "prod-api-critical" \
    --alarm-types CompositeAlarm \
    --query 'CompositeAlarms[0].AlarmRule'
  ```

**하위 알람이 INSUFFICIENT_DATA 상태**
- 증상: EC2 인스턴스가 없을 때 알람이 INSUFFICIENT_DATA로 빠짐
- 원인: Metric Alarm의 `treat_missing_data` 설정 미흡
- 해결: 상황에 따라 `notBreaching` 또는 `breaching` 설정
  ```hcl
  resource "aws_cloudwatch_metric_alarm" "cpu_high" {
    # ...
    treat_missing_data = "notBreaching"  # 데이터 없으면 정상으로 간주
  }
  ```

### 3.2 자주 발생하는 문제 (Q&A)

- Q: Composite Alarm에서 Auto Scaling 액션을 설정할 수 있나요?
- A: 불가. Composite Alarm은 SNS 액션만 지원. Auto Scaling은 Metric Alarm에 직접 설정

- Q: 최대 몇 단계까지 중첩 가능한가요?
- A: Composite Alarm 안에 Composite Alarm 중첩 가능. Rule Expression 길이 제한(10,240자) 내에서 사용

- Q: 크로스 계정 알람을 Rule에 포함할 수 있나요?
- A: 동일 계정/리전의 알람만 포함 가능. 크로스 계정은 지원하지 않음

## 4. 모니터링 및 알람
```hcl
# Composite Alarm 상태 변경을 EventBridge로 감지
resource "aws_cloudwatch_event_rule" "composite_alarm_change" {
  name        = "composite-alarm-state-change"
  description = "복합 알람 상태 변경 감지"

  event_pattern = jsonencode({
    source      = ["aws.cloudwatch"]
    detail-type = ["CloudWatch Alarm State Change"]
    detail = {
      alarmName = ["prod-api-critical", "system-critical"]
      state = {
        value = ["ALARM"]
      }
    }
  })
}

resource "aws_cloudwatch_event_target" "notify_slack" {
  rule = aws_cloudwatch_event_rule.composite_alarm_change.name
  arn  = aws_sns_topic.slack_notification.arn
}
```

## 5. TIP
- **Rule 설계 원칙**: 리소스 포화(Saturation) + 오류율(Error) + 지연(Latency) 3가지를 조합하는 RED 메서드 기반으로 구성
- Composite Alarm은 액션이 SNS만 지원하므로 Lambda를 SNS 구독자로 연결하면 PagerDuty, Slack, Jira 티켓 생성 등 다양한 후속 자동화 가능
- 하위 알람에 `alarm_description`을 상세히 작성해두면 복합 알람 발동 시 원인 파악이 빠름
- 관련 문서: [Composite Alarms 공식 가이드](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/Create_Composite_Alarm.html)
