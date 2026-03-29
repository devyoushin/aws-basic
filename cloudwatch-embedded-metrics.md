# CloudWatch Embedded Metric Format (EMF)

## 1. 개요
- EMF(Embedded Metric Format)는 로그 메시지 안에 CloudWatch 지표를 JSON 구조로 삽입하여, 별도 API 호출 없이 로그 한 줄로 지표와 로그를 동시에 발행하는 방식
- Lambda, ECS, EKS 등 컨테이너 환경에서 `put-metric-data` API 호출 오버헤드 없이 고카디널리티 지표 수집 가능
- 비즈니스 지표(주문 금액, 사용자 행동 등)를 코드 변경 최소화로 CloudWatch에 통합

## 2. 설명
### 2.1 핵심 개념

**EMF vs 기존 방식 비교**
| 항목 | PutMetricData API | EMF (로그 기반) |
|------|------------------|----------------|
| 발행 방식 | API 직접 호출 | CloudWatch Logs에 JSON 로그 |
| 오버헤드 | API 호출 비용/지연 | 로그 수집과 통합 |
| 카디널리티 | Dimension 10개 제한 | 최대 30개 Dimension |
| 비용 | put-metric-data 건수 과금 | 로그 수집 + 지표 생성 |
| 로그 연동 | 별도 처리 필요 | 로그와 지표 자동 연동 |

**EMF JSON 구조**
```json
{
  "_aws": {
    "Timestamp": 1609459200000,
    "CloudWatchMetrics": [
      {
        "Namespace": "MyApp/Orders",
        "Dimensions": [["Service", "Environment"]],
        "Metrics": [
          { "Name": "OrderCount", "Unit": "Count" },
          { "Name": "OrderValue", "Unit": "None" },
          { "Name": "ProcessingTime", "Unit": "Milliseconds" }
        ]
      }
    ]
  },
  "Service": "order-service",
  "Environment": "prod",
  "OrderCount": 1,
  "OrderValue": 59900,
  "ProcessingTime": 142,
  "userId": "user-12345",
  "orderId": "ord-67890"
}
```

**핵심 규칙**
- `_aws.CloudWatchMetrics[].Dimensions` — 배열의 배열 (여러 Dimension 조합 지원)
- `_aws.Timestamp` — Unix epoch milliseconds
- Metric 값은 루트 레벨 필드로 정의
- 나머지 필드는 로그에만 저장 (지표에 미포함)

### 2.2 실무 적용 코드

**Lambda — EMF 직접 출력 (최소 의존성)**
```python
import json
import time
import boto3

def emit_metric(namespace, metrics, dimensions, properties=None):
    """EMF 형식으로 로그 출력"""
    timestamp = int(time.time() * 1000)

    metric_definitions = [
        {"Name": name, "Unit": unit}
        for name, unit in metrics.items()
    ]

    dimension_keys = list(dimensions.keys())

    log_entry = {
        "_aws": {
            "Timestamp": timestamp,
            "CloudWatchMetrics": [
                {
                    "Namespace": namespace,
                    "Dimensions": [dimension_keys],
                    "Metrics": metric_definitions
                }
            ]
        }
    }

    # 차원 값 추가
    log_entry.update(dimensions)

    # 지표 값 추가
    for metric_name in metrics:
        log_entry[metric_name] = metrics[metric_name]

    # 추가 컨텍스트 (로그에만 기록, 지표에 미포함)
    if properties:
        log_entry.update(properties)

    # EMF는 반드시 단일 JSON 줄로 출력
    print(json.dumps(log_entry))


def lambda_handler(event, context):
    start_time = time.time()

    # 비즈니스 로직 실행
    order_id = event.get("orderId")
    order_value = event.get("amount", 0)

    try:
        # 주문 처리 로직
        process_order(event)
        processing_time = (time.time() - start_time) * 1000

        # 성공 지표 발행
        emit_metric(
            namespace="MyApp/Orders",
            metrics={
                "OrderCount": {"Name": "OrderCount", "Unit": "Count"},
                "OrderValue": {"Name": "OrderValue", "Unit": "None"},
                "ProcessingTime": {"Name": "ProcessingTime", "Unit": "Milliseconds"}
            },
            dimensions={
                "Service": "order-service",
                "Environment": "prod",
                "PaymentMethod": event.get("paymentMethod", "unknown")
            },
            properties={
                "orderId": order_id,
                "userId": event.get("userId"),
                "success": True
            }
        )

    except Exception as e:
        # 실패 지표 발행
        emit_metric(
            namespace="MyApp/Orders",
            metrics={"OrderFailureCount": {"Name": "OrderFailureCount", "Unit": "Count"}},
            dimensions={"Service": "order-service", "Environment": "prod"},
            properties={"orderId": order_id, "error": str(e), "success": False}
        )
        raise
```

**Lambda Powertools EMF (권장 방식)**
```python
from aws_lambda_powertools import Logger, Metrics
from aws_lambda_powertools.metrics import MetricUnit
from aws_lambda_powertools.utilities.typing import LambdaContext

logger = Logger(service="order-service")
metrics = Metrics(namespace="MyApp/Orders", service="order-service")

@metrics.log_metrics(capture_cold_start_metric=True)
@logger.inject_lambda_context
def lambda_handler(event: dict, context: LambdaContext):
    metrics.add_dimension(name="Environment", value="prod")
    metrics.add_dimension(name="PaymentMethod", value=event.get("paymentMethod", "card"))

    metrics.add_metric(name="OrderCount", unit=MetricUnit.Count, value=1)
    metrics.add_metric(name="OrderValue", unit=MetricUnit.NoUnit, value=event.get("amount", 0))

    logger.info("Order processed", extra={
        "orderId": event.get("orderId"),
        "userId": event.get("userId")
    })
```

**Node.js — aws-embedded-metrics 라이브러리**
```javascript
const { metricScope, Unit } = require('aws-embedded-metrics');

exports.handler = metricScope(metrics => async (event, context) => {
  metrics.setNamespace('MyApp/Orders');
  metrics.putDimensions({
    Service: 'order-service',
    Environment: process.env.ENVIRONMENT
  });

  const startTime = Date.now();

  try {
    await processOrder(event);
    const duration = Date.now() - startTime;

    metrics.putMetric('OrderCount', 1, Unit.Count);
    metrics.putMetric('OrderValue', event.amount, Unit.None);
    metrics.putMetric('ProcessingTime', duration, Unit.Milliseconds);

    // 로그에 추가 컨텍스트 (지표 아님)
    metrics.setProperty('orderId', event.orderId);
    metrics.setProperty('userId', event.userId);

  } catch (error) {
    metrics.putMetric('OrderFailureCount', 1, Unit.Count);
    throw error;
  }
});
```

**ECS/EKS — 사이드카 없이 직접 stdout 출력**
```python
# FastAPI 예시
import json
import time
from fastapi import FastAPI, Request

app = FastAPI()

def emit_emf(namespace: str, metric_name: str, value: float, unit: str,
             dimensions: dict, properties: dict = None):
    log = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": namespace,
                "Dimensions": [list(dimensions.keys())],
                "Metrics": [{"Name": metric_name, "Unit": unit}]
            }]
        },
        metric_name: value,
        **dimensions,
        **(properties or {})
    }
    print(json.dumps(log), flush=True)

@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = (time.time() - start) * 1000

    emit_emf(
        namespace="MyApp/API",
        metric_name="RequestDuration",
        value=duration,
        unit="Milliseconds",
        dimensions={
            "Service": "api-service",
            "Environment": "prod",
            "Method": request.method,
            "Path": request.url.path
        },
        properties={
            "status_code": response.status_code,
            "user_agent": request.headers.get("user-agent", "")
        }
    )

    emit_emf(
        namespace="MyApp/API",
        metric_name="RequestCount",
        value=1,
        unit="Count",
        dimensions={
            "Service": "api-service",
            "Environment": "prod",
            "StatusCode": str(response.status_code)
        }
    )

    return response
```

**여러 Dimension 조합 (배열의 배열)**
```json
{
  "_aws": {
    "CloudWatchMetrics": [{
      "Namespace": "MyApp",
      "Dimensions": [
        ["Service"],
        ["Service", "Environment"],
        ["Service", "Environment", "Region"]
      ],
      "Metrics": [{"Name": "RequestCount", "Unit": "Count"}]
    }]
  },
  "Service": "api",
  "Environment": "prod",
  "Region": "ap-northeast-2",
  "RequestCount": 1
}
```
→ 3가지 Dimension 조합으로 지표가 각각 발행됨

**Terraform — EMF 로그 그룹 및 알람**
```hcl
resource "aws_cloudwatch_log_group" "app" {
  name              = "/aws/lambda/order-service"
  retention_in_days = 30
}

# EMF로 발행된 지표로 알람 생성
resource "aws_cloudwatch_metric_alarm" "order_failure" {
  alarm_name          = "order-service-failure-rate"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "OrderFailureCount"
  namespace           = "MyApp/Orders"
  period              = 300
  statistic           = "Sum"
  threshold           = 5
  alarm_description   = "주문 실패 5건 초과"
  alarm_actions       = [aws_sns_topic.ops.arn]
  dimensions = {
    Service     = "order-service"
    Environment = "prod"
  }
}
```

### 2.3 보안/비용 Best Practice
- EMF 지표는 **CloudWatch Logs 비용** + **커스텀 지표 비용** 모두 발생
- 고카디널리티 Dimension(userId, orderId 등)은 지표 Dimension에서 제외하고 properties로만 기록
- `_aws` 블록이 없는 일반 JSON 로그와 함께 사용 가능 — 같은 로그 그룹에서 혼재
- Lambda의 경우 Powertools 라이브러리 사용 권장 — Cold Start 지표, 구조화 로깅 통합

## 3. 트러블슈팅
### 3.1 주요 이슈

**지표가 CloudWatch에 나타나지 않음**
- 증상: stdout에 EMF JSON 출력했으나 지표 없음
- 원인: JSON 형식 오류 또는 `_aws` 필드 누락
- 해결:
  ```bash
  # 로그에서 EMF 구조 확인
  aws logs filter-log-events \
    --log-group-name /aws/lambda/order-service \
    --filter-pattern '{$.\"_aws\" = *}' \
    --limit 5

  # 지표 발행 여부 확인 (5분 지연 있음)
  aws cloudwatch list-metrics --namespace "MyApp/Orders"
  ```

**카디널리티 폭발로 비용 증가**
- 증상: 커스텀 지표 수가 예상보다 수백 배 증가
- 원인: userId, requestId 등 고유값을 Dimension으로 사용
- 해결: Dimension은 "서비스", "환경", "메서드" 등 저카디널리티 값만, 고유값은 properties로 이동

### 3.2 자주 발생하는 문제 (Q&A)

- Q: EMF와 Metric Filter 중 어떤 걸 써야 하나요?
- A: 구조화된 JSON 로그에서 복잡한 집계가 필요하면 EMF, 단순 패턴 매칭으로 충분하면 Metric Filter

- Q: CloudWatch Logs가 아닌 Kinesis로 EMF를 보낼 수 있나요?
- A: 가능. `AWS_EMF_AGENT_ENDPOINT` 환경변수로 에이전트 엔드포인트 지정

## 4. 모니터링 및 알람
```bash
# EMF로 발행된 지표 목록 확인
aws cloudwatch list-metrics \
  --namespace "MyApp/Orders" \
  --query 'Metrics[*].{Name:MetricName,Dims:Dimensions}'

# 특정 지표 최근 데이터 조회
aws cloudwatch get-metric-statistics \
  --namespace "MyApp/Orders" \
  --metric-name "OrderCount" \
  --dimensions Name=Service,Value=order-service Name=Environment,Value=prod \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 300 \
  --statistics Sum
```

## 5. TIP
- **Cold Start 지표**: Lambda Powertools의 `capture_cold_start_metric=True`로 자동 추적 — Provisioned Concurrency 결정에 활용
- Dimension 값은 256자 이하, 지표명은 256자 이하 — 길면 잘림
- 배치 처리 시 한 번에 여러 EMF 로그 출력 가능 — 각 줄이 독립적인 지표
- JSON 로그를 CloudWatch Logs Insights에서 바로 쿼리 가능 — 지표와 로그 컨텍스트 동시 분석
- 관련 문서: [EMF 스펙](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Embedded_Metric_Format_Specification.html)
