# CloudWatch Metric Math

## 1. 개요
- Metric Math는 여러 CloudWatch 지표를 수식으로 결합해 새로운 가상 지표를 만드는 기능
- 예: 에러율 = (5XX 에러 수 / 전체 요청 수) × 100 — 기존 지표만으로는 표현 불가한 비율/복합 지표 생성
- 별도 커스텀 지표 발행 없이 대시보드와 알람에 바로 사용 가능하여 비용 효율적

## 2. 설명
### 2.1 핵심 개념

**지원 연산자 및 함수**
| 함수/연산자 | 설명 | 예시 |
|------------|------|------|
| `+`, `-`, `*`, `/` | 사칙연산 | `m1 / m2 * 100` |
| `ABS(m)` | 절댓값 | |
| `CEIL(m)`, `FLOOR(m)` | 올림/내림 | |
| `MIN(m)`, `MAX(m)` | 시계열 내 최솟값/최댓값 | |
| `AVG(m)` | 평균 | |
| `SUM(METRICS())` | 여러 지표의 합 | 다중 인스턴스 합산 |
| `RATE(m)` | 변화율 (초당) | `RATE(m1)` |
| `DIFF(m)` | 이전 값과 차이 | |
| `IF(condition, trueVal, falseVal)` | 조건식 | `IF(m1 > 100, 1, 0)` |
| `FILL(m, value)` | 누락 데이터 채우기 | `FILL(m1, 0)` |
| `SEARCH(query, stat, period)` | 패턴으로 지표 동적 검색 | |
| `METRICS()` | 현재 그래프의 모든 지표 | `SUM(METRICS())` |
| `PERIOD(m)` | 지표의 Period 반환 | |
| `ANOMALY_DETECTION_BAND(m)` | 이상 감지 밴드 생성 | |

**데이터 타입**
- **스칼라**: 단일 숫자 값 (예: `100`, `AVG(m1)`)
- **시계열**: 타임스탬프-값 쌍의 배열 (대부분의 지표)
- 스칼라 × 시계열 → 시계열 (브로드캐스팅 적용)

### 2.2 실무 적용 코드

**ALB 에러율 계산**
```
# 수식 예시
m1 = AWS/ApplicationELB > HTTPCode_Target_5XX_Count (Sum)
m2 = AWS/ApplicationELB > RequestCount (Sum)
e1 = (m1 / m2) * 100   # 5XX 에러율 (%)
```

**Terraform — Metric Math 알람 (에러율 기반)**
```hcl
resource "aws_cloudwatch_metric_alarm" "error_rate" {
  alarm_name          = "prod-alb-error-rate-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  threshold           = 5  # 5% 초과 시 알람
  alarm_description   = "ALB 5XX 에러율 5% 초과"
  alarm_actions       = [aws_sns_topic.oncall.arn]

  metric_query {
    id          = "e1"
    expression  = "(m1 / m2) * 100"
    label       = "5XX Error Rate (%)"
    return_data = true
  }

  metric_query {
    id = "m1"
    metric {
      metric_name = "HTTPCode_Target_5XX_Count"
      namespace   = "AWS/ApplicationELB"
      period      = 60
      stat        = "Sum"
      dimensions = {
        LoadBalancer = aws_lb.prod.arn_suffix
      }
    }
  }

  metric_query {
    id = "m2"
    metric {
      metric_name = "RequestCount"
      namespace   = "AWS/ApplicationELB"
      period      = 60
      stat        = "Sum"
      dimensions = {
        LoadBalancer = aws_lb.prod.arn_suffix
      }
    }
  }
}
```

**EKS Node — 메모리 사용률 (% 변환)**
```hcl
# node_memory_MemTotal_bytes와 node_memory_MemAvailable_bytes를 조합
resource "aws_cloudwatch_metric_alarm" "node_memory" {
  alarm_name          = "eks-node-memory-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  threshold           = 85

  metric_query {
    id         = "e1"
    expression = "((m1 - m2) / m1) * 100"
    label      = "Memory Usage (%)"
    return_data = true
  }

  metric_query {
    id = "m1"
    metric {
      metric_name = "node_memory_MemTotal_bytes"
      namespace   = "ContainerInsights"
      period      = 60
      stat        = "Average"
      dimensions = {
        ClusterName = "prod-cluster"
      }
    }
  }

  metric_query {
    id = "m2"
    metric {
      metric_name = "node_memory_MemAvailable_bytes"
      namespace   = "ContainerInsights"
      period      = 60
      stat        = "Average"
      dimensions = {
        ClusterName = "prod-cluster"
      }
    }
  }
}
```

**SEARCH 함수 — 여러 인스턴스 합산**
```
# 모든 EC2 인스턴스의 CPU 합산 (동적으로 인스턴스 추가/제거 반영)
SUM(SEARCH('{AWS/EC2,InstanceId} MetricName="CPUUtilization"', 'Average', 300))

# 특정 Auto Scaling 그룹 인스턴스들의 네트워크 In 합산
SUM(SEARCH('{AWS/EC2,InstanceId} AutoScalingGroupName="prod-asg" MetricName="NetworkIn"', 'Sum', 60))
```

**IF 함수 — 조건부 지표 (0 나누기 방지)**
```
# m2(요청 수)가 0일 때 나누기 방지
IF(m2 > 0, (m1 / m2) * 100, 0)
```

**ANOMALY_DETECTION_BAND — 이상 감지 알람**
```hcl
resource "aws_cloudwatch_metric_alarm" "latency_anomaly" {
  alarm_name          = "prod-latency-anomaly"
  comparison_operator = "GreaterThanUpperThreshold"
  evaluation_periods  = 3
  threshold_metric_id = "e1"
  alarm_description   = "레이턴시 이상 감지 (ML 기반)"

  metric_query {
    id          = "e1"
    expression  = "ANOMALY_DETECTION_BAND(m1, 2)"  # 표준편차 2배 밴드
    label       = "TargetResponseTime (expected)"
    return_data = true
  }

  metric_query {
    id          = "m1"
    return_data = true
    metric {
      metric_name = "TargetResponseTime"
      namespace   = "AWS/ApplicationELB"
      period      = 60
      stat        = "p99"
      dimensions = {
        LoadBalancer = aws_lb.prod.arn_suffix
      }
    }
  }
}
```

**FILL — 누락 데이터를 0으로 채워 정확한 에러율 계산**
```
e1 = (FILL(m1, 0) / FILL(m2, 1)) * 100
# m1: 에러 수 (없으면 0), m2: 요청 수 (없으면 1 — 0 나누기 방지)
```

**RATE — 초당 변화율**
```
# EBS 읽기 바이트의 초당 처리량 계산
RATE(m1)  # m1 = VolumeReadBytes (Sum, 60s) → 초당 바이트
```

**AWS CLI — Metric Math로 지표 조회**
```bash
aws cloudwatch get-metric-data \
  --metric-data-queries '[
    {
      "Id": "e1",
      "Expression": "(m1 / m2) * 100",
      "Label": "ErrorRate"
    },
    {
      "Id": "m1",
      "MetricStat": {
        "Metric": {
          "Namespace": "AWS/ApplicationELB",
          "MetricName": "HTTPCode_Target_5XX_Count",
          "Dimensions": [{"Name": "LoadBalancer", "Value": "app/prod-alb/abc123"}]
        },
        "Period": 60,
        "Stat": "Sum"
      }
    },
    {
      "Id": "m2",
      "MetricStat": {
        "Metric": {
          "Namespace": "AWS/ApplicationELB",
          "MetricName": "RequestCount",
          "Dimensions": [{"Name": "LoadBalancer", "Value": "app/prod-alb/abc123"}]
        },
        "Period": 60,
        "Stat": "Sum"
      }
    }
  ]' \
  --start-time 2024-01-01T00:00:00Z \
  --end-time 2024-01-01T01:00:00Z
```

### 2.3 보안/비용 Best Practice
- Metric Math 표현식 자체는 추가 비용 없음 — 기반 지표 조회 비용만 발생
- SEARCH 함수는 매번 동적으로 지표를 검색하므로 대시보드 갱신 빈도 최소화
- 알람에 Metric Math 사용 시 `return_data = true`는 반드시 최종 결과 표현식에만 설정
- ANOMALY_DETECTION은 학습 데이터가 최소 2주 이상 있어야 정확도 높아짐

## 3. 트러블슈팅
### 3.1 주요 이슈

**알람이 INSUFFICIENT_DATA로 계속 유지**
- 증상: Metric Math 알람이 정상 데이터가 있어도 INSUFFICIENT_DATA
- 원인: 기반 지표 중 하나라도 데이터가 없으면 수식 결과가 null
- 해결: `FILL(m1, 0)` 사용하거나 `treat_missing_data = "notBreaching"` 설정

**SEARCH 함수 결과가 대시보드마다 다름**
- 증상: 동일 SEARCH 쿼리인데 다른 값 반환
- 원인: 대시보드의 시간 범위나 Period가 다름
- 해결: SEARCH 3번째 파라미터(period)를 명시적으로 지정

**수식 결과가 NaN 또는 Infinity**
- 원인: 0으로 나누기 발생 (m2 = 0인 순간)
- 해결: `IF(m2 > 0, m1/m2, 0)` 패턴 적용

### 3.2 자주 발생하는 문제 (Q&A)

- Q: Metric Math 결과를 커스텀 지표로 저장할 수 있나요?
- A: 직접 저장 불가. Lambda나 CWAgent에서 계산 후 `put-metric-data`로 별도 발행 필요

- Q: Period를 여러 지표에서 다르게 설정해도 되나요?
- A: 가능하지만 권장하지 않음. CloudWatch가 자동으로 업샘플링하는데 결과가 예상과 다를 수 있음

## 4. 모니터링 및 알람

**USE 메서드 기반 핵심 알람 세트**
```hcl
# Utilization (사용률)
# Saturation (포화도)
# Errors (에러율)

# EC2 CPU 사용률 알람 (단순)
resource "aws_cloudwatch_metric_alarm" "cpu_utilization" {
  alarm_name          = "ec2-cpu-high"
  metric_name         = "CPUUtilization"
  namespace           = "AWS/EC2"
  period              = 60
  statistic           = "Average"
  threshold           = 80
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
}

# ALB 에러율 알람 (Metric Math)
resource "aws_cloudwatch_metric_alarm" "alb_error_rate" {
  alarm_name          = "alb-5xx-error-rate"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  threshold           = 1  # 1% 초과

  metric_query {
    id         = "e1"
    expression = "IF(m2 > 0, (m1 / m2) * 100, 0)"
    return_data = true
  }
  # ... m1, m2 metric_query 블록
}
```

## 5. TIP
- **`id` 네이밍 규칙**: 소문자 + 숫자만 허용. `m1`, `m2`, `e1`처럼 단순하게 유지
- 대시보드에서 "Math expression" 탭을 선택하면 UI로 수식 테스트 가능 (코드 없이 확인)
- `SUM(METRICS())` 패턴은 ASG 인스턴스 합산에 매우 유용 — 인스턴스 추가/제거 시 자동 반영
- ANOMALY_DETECTION 밴드는 요일/시간대 패턴을 자동 학습 — 주말 트래픽 감소로 인한 오탐 방지
- 관련 문서: [Metric Math 함수 레퍼런스](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/using-metric-math.html)
