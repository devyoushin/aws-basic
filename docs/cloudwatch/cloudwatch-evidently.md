# CloudWatch Evidently (Feature Flag & A/B 테스트)

## 1. 개요
- CloudWatch Evidently는 Feature Flag(기능 플래그)와 A/B 테스트(실험)를 CloudWatch 지표와 통합해 관리하는 서비스
- 코드 배포 없이 특정 사용자 그룹에게만 기능을 점진적으로 활성화하거나, 여러 변형을 비교해 데이터 기반 의사결정 가능
- CloudWatch RUM, X-Ray와 연동해 기능 변경이 성능/에러율에 미치는 영향을 자동 측정

## 2. 설명
### 2.1 핵심 개념

**핵심 용어**
| 용어 | 설명 |
|------|------|
| Project | Feature Flag/실험을 관리하는 최상위 단위 |
| Feature | 토글 가능한 기능 단위 (변형 포함) |
| Variation | Feature의 변형 (True/False 또는 다양한 설정값) |
| Launch | 점진적 트래픽 분할 배포 (Canary 배포) |
| Experiment | A/B 테스트 — 지표 기반 통계 유의성 검증 |
| Segment | 사용자 그룹 필터 (userId, 디바이스, 국가 등) |

**Feature Flag vs Experiment**
| 항목 | Feature (Launch) | Experiment |
|------|-----------------|------------|
| 목적 | 점진적 배포, Kill Switch | 통계적 A/B 테스트 |
| 트래픽 | 수동 설정 (10% → 50% → 100%) | 자동 분할 (50/50) |
| 결과 | 배포 완료/롤백 | 통계적 승자 결정 |
| 기간 | 무기한 | 고정 실험 기간 |

### 2.2 실무 적용 코드

**Terraform — Evidently 프로젝트 및 Feature 생성**
```hcl
# Evidently 프로젝트
resource "aws_evidently_project" "main" {
  name        = "prod-web-app"
  description = "프로덕션 웹 앱 Feature Flag 및 A/B 테스트"

  data_delivery {
    # 실험 데이터 S3 저장
    s3_destination {
      bucket = aws_s3_bucket.evidently.bucket
      prefix = "evidently-data"
    }

    # 또는 CloudWatch Logs 저장
    # cloudwatch_logs {
    #   log_group = "/evidently/prod-web-app"
    # }
  }

  tags = {
    Environment = "prod"
    Team        = "product"
  }
}

# Feature — 새 결제 UI 플래그
resource "aws_evidently_feature" "new_checkout_ui" {
  name        = "new-checkout-ui"
  project     = aws_evidently_project.main.name
  description = "새로운 단계별 결제 UI (기존: 단일 페이지)"

  # 기본값 (Feature OFF)
  default_variation = "control"

  variations {
    name = "control"
    value {
      bool_value = false
    }
  }

  variations {
    name = "treatment"
    value {
      bool_value = true
    }
  }

  # 평가 전략: ALL_RULES (Launch/Experiment 규칙 적용)
  evaluation_strategy = "ALL_RULES"

  tags = {
    Team   = "checkout"
    Ticket = "PROD-1234"
  }
}

# Feature — 다양한 버튼 색상 테스트
resource "aws_evidently_feature" "cta_button_color" {
  name    = "cta-button-color"
  project = aws_evidently_project.main.name

  default_variation = "blue"

  variations {
    name = "blue"
    value { string_value = "#0066CC" }
  }
  variations {
    name = "green"
    value { string_value = "#28A745" }
  }
  variations {
    name = "orange"
    value { string_value = "#FF6B35" }
  }
}

# Launch — 점진적 배포 (10% → 50% → 100%)
resource "aws_evidently_launch" "new_checkout_rollout" {
  name    = "new-checkout-ui-rollout"
  project = aws_evidently_project.main.name

  groups {
    feature   = aws_evidently_feature.new_checkout_ui.name
    variation = "treatment"
    name      = "treatment-group"
  }

  scheduled_splits_config {
    steps {
      group_weights = {
        treatment-group = 10000  # 10% (0~100000 범위)
      }
      start_time = "2024-02-01T09:00:00Z"
    }

    steps {
      group_weights = {
        treatment-group = 50000  # 50%
      }
      start_time = "2024-02-08T09:00:00Z"
    }

    steps {
      group_weights = {
        treatment-group = 100000  # 100%
      }
      start_time = "2024-02-15T09:00:00Z"
    }
  }

  metric_monitors {
    metric_definition {
      name       = "checkout-error-rate"
      entity_id_key = "userId"
      value_key  = "errorCount"
      event_pattern = jsonencode({
        detail-type = ["checkout-error"]
      })
    }
  }
}

# Experiment — A/B 테스트
resource "aws_evidently_experiment" "button_color_test" {
  name        = "cta-button-color-experiment"
  project     = aws_evidently_project.main.name
  description = "결제 버튼 색상이 전환율에 미치는 영향"

  online_ab_config {
    control_treatment_name = "blue"

    treatment_weights = {
      blue   = 33333
      green  = 33333
      orange = 33334
    }
  }

  metric_goals {
    desired_change = "INCREASE"
    metric_definition {
      name          = "purchase-conversion"
      entity_id_key = "userId"
      value_key     = "converted"
      event_pattern = jsonencode({
        detail-type = ["purchase-complete"]
      })
      unit_label = "conversion"
    }
  }

  metric_goals {
    desired_change = "DECREASE"
    metric_definition {
      name          = "checkout-abandonment"
      entity_id_key = "userId"
      value_key     = "abandoned"
      event_pattern = jsonencode({
        detail-type = ["checkout-abandoned"]
      })
    }
  }

  # 샘플 크기 설정
  sampling_rate = 10000  # 10% 트래픽만 실험 대상

  # 실험 기간
  # start_time / stop_time은 API로 제어
}
```

**Python — SDK를 통한 Feature Evaluation**
```python
import boto3
import json
import random

evidently = boto3.client('evidently', region_name='ap-northeast-2')

def get_feature_variation(user_id: str, feature_name: str, project: str = 'prod-web-app') -> dict:
    """사용자별 Feature 변형 평가"""
    try:
        response = evidently.evaluate_feature(
            entityId=user_id,
            feature=feature_name,
            project=project,
            evaluationContext=json.dumps({
                "userId": user_id,
                "country": "KR",
                "deviceType": "mobile"
            })
        )

        return {
            "variation": response["variation"],
            "value": response["value"],
            "reason": response["reason"]
        }

    except evidently.exceptions.ResourceNotFoundException:
        # Feature가 없으면 기본값 반환
        return {"variation": "control", "value": False, "reason": "DEFAULT"}


def batch_evaluate_features(user_id: str, project: str = 'prod-web-app') -> dict:
    """여러 Feature 일괄 평가"""
    requests = [
        {"entityId": user_id, "feature": "new-checkout-ui"},
        {"entityId": user_id, "feature": "cta-button-color"},
        {"entityId": user_id, "feature": "recommendation-engine"},
    ]

    response = evidently.batch_evaluate_feature(
        project=project,
        requests=requests
    )

    return {
        result["feature"]: {
            "variation": result["variation"],
            "value": list(result["value"].values())[0]
        }
        for result in response["results"]
    }


def put_project_events(user_id: str, event_type: str, value: float, project: str = 'prod-web-app'):
    """실험 지표 이벤트 발행"""
    evidently.put_project_events(
        project=project,
        events=[{
            "timestamp": __import__('datetime').datetime.utcnow(),
            "type": "aws.evidently.custom",
            "data": json.dumps({
                "entityId": user_id,
                "details": {
                    event_type: value
                }
            })
        }]
    )


# FastAPI 예시
from fastapi import FastAPI, Request

app = FastAPI()

@app.get("/checkout")
async def checkout_page(request: Request):
    user_id = request.headers.get("X-User-ID", "anonymous")

    # Feature Flag 평가
    feature = get_feature_variation(user_id, "new-checkout-ui")

    if feature["value"]:
        return {"template": "checkout_v2", "variation": "treatment"}
    else:
        return {"template": "checkout_v1", "variation": "control"}


@app.post("/purchase/complete")
async def purchase_complete(request: Request, order_id: str, amount: float):
    user_id = request.headers.get("X-User-ID")

    # 구매 완료 이벤트 → Evidently 실험 지표로 기록
    put_project_events(user_id, "purchase-conversion", 1.0)
    put_project_events(user_id, "purchase-amount", amount)

    return {"status": "success", "orderId": order_id}
```

**Node.js — Feature Flag 평가**
```javascript
const { CloudWatchEvidently } = require('@aws-sdk/client-cloudwatch-evidently');

const evidently = new CloudWatchEvidently({ region: 'ap-northeast-2' });

async function isFeatureEnabled(userId, featureName, project = 'prod-web-app') {
  try {
    const response = await evidently.evaluateFeature({
      entityId: userId,
      feature: featureName,
      project,
      evaluationContext: JSON.stringify({
        userId,
        userAgent: 'mobile-app/2.0'
      })
    });

    return {
      enabled: response.value?.boolValue ?? false,
      variation: response.variation,
      reason: response.reason
    };
  } catch (error) {
    console.error('Feature evaluation failed:', error);
    return { enabled: false, variation: 'control', reason: 'ERROR_FALLBACK' };
  }
}

// 사용 예
const checkout = await isFeatureEnabled(req.userId, 'new-checkout-ui');
if (checkout.enabled) {
  renderNewCheckout();
} else {
  renderLegacyCheckout();
}
```

**AWS CLI — 실험 관리**
```bash
# 프로젝트 목록
aws evidently list-projects

# Feature 목록
aws evidently list-features --project prod-web-app

# 실험 시작
aws evidently start-experiment \
  --project prod-web-app \
  --experiment cta-button-color-experiment \
  --analysis-complete-time "2024-02-28T23:59:59Z"

# 실험 결과 조회
aws evidently get-experiment-results \
  --project prod-web-app \
  --experiment cta-button-color-experiment \
  --metric-names purchase-conversion \
  --treatment-names green orange \
  --base-stat Mean

# Launch 중지 (즉시 롤백)
aws evidently stop-launch \
  --project prod-web-app \
  --launch new-checkout-ui-rollout \
  --desired-state CANCELLED
```

### 2.3 보안/비용 Best Practice
- Feature 평가는 클라이언트가 아닌 서버 사이드에서 수행 — 클라이언트 노출 시 변형 조작 가능
- 실험 완료 후 Feature를 코드에서 제거하는 날짜를 Ticket에 기록 — Feature Flag 부채 방지
- **비용**: 평가 건수 100만 건당 $1, 이벤트 100만 건당 $1
- 대규모 트래픽 시 `evaluateFeature` 결과를 캐싱 (Redis/로컬 TTL 30~60초)
- 실험 대상 기능은 반드시 Feature Flag와 함께 배포 → 문제 발생 시 즉시 OFF 가능

## 3. 트러블슈팅
### 3.1 주요 이슈

**Feature 평가가 항상 기본값 반환**
- 증상: `reason: "DEFAULT"` — 변형 배포 안 됨
- 원인: Launch/Experiment가 시작되지 않았거나 entityId가 세그먼트에 미해당
- 해결:
  ```bash
  # Launch 상태 확인
  aws evidently get-launch \
    --project prod-web-app \
    --launch new-checkout-ui-rollout \
    --query 'launch.status'

  # Feature 현재 상태 확인
  aws evidently get-feature \
    --project prod-web-app \
    --feature new-checkout-ui
  ```

**실험 결과에 통계적 유의성 없음**
- 원인: 샘플 크기 부족 또는 실험 기간 짧음
- 해결: 최소 샘플 크기 계산기로 필요 기간 산출. 일반적으로 전환율 차이 5% 감지에 주당 수천 건 필요

### 3.2 자주 발생하는 문제 (Q&A)

- Q: Evidently를 외부 사용자(비로그인)에도 사용할 수 있나요?
- A: 가능. `entityId`로 쿠키/세션 ID 등 익명 식별자 사용. 단 브라우저 전환 시 변형이 바뀔 수 있음

- Q: A/B 테스트 결과를 자동으로 승자 적용할 수 있나요?
- A: 자동 적용 미지원. 결과 조회 후 수동으로 Launch 트래픽 100% 전환 또는 Feature 코드 제거 필요

## 4. 모니터링 및 알람
```bash
# Evidently 지표를 CloudWatch 대시보드에 추가
aws cloudwatch put-dashboard \
  --dashboard-name "evidently-experiments" \
  --dashboard-body '{
    "widgets": [{
      "type": "metric",
      "properties": {
        "title": "Feature Evaluation Count",
        "metrics": [
          ["CloudWatchEvidently/projects", "FeatureEvaluationCount",
            "project", "prod-web-app", {"stat": "Sum"}]
        ]
      }
    }]
  }'
```

## 5. TIP
- **Kill Switch 패턴**: 모든 새 기능에 Evidently Feature Flag를 기본 탑재 — 장애 시 배포 없이 즉시 OFF
- Launch의 `metric_monitors`에 에러율 임계값 설정 시 자동 롤백 트리거 가능
- 실험 결과를 S3에 저장 후 Athena로 커스텀 분석 가능 (세그먼트별 전환율 등)
- CloudWatch RUM과 연동 시 브라우저에서 직접 Feature 평가 및 이벤트 발행 가능
- 관련 문서: [CloudWatch Evidently 공식 가이드](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-Evidently.html)
