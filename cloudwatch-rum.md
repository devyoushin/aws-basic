# CloudWatch RUM (Real User Monitoring)

## 1. 개요
- CloudWatch RUM(Real User Monitoring)은 실제 사용자 브라우저에서 수집한 프론트엔드 성능 데이터를 CloudWatch로 전송하는 관찰성 서비스
- Core Web Vitals(LCP, FID, CLS), JavaScript 에러, API 호출 지연, 페이지 로드 시간 등 실제 사용자 경험 지표 제공
- 서버 사이드 지표만으로는 알 수 없는 "클라이언트에서 체감하는 성능"을 파악할 수 있어 UX 개선 의사결정에 핵심

## 2. 설명
### 2.1 핵심 개념

**수집 데이터 유형**
| 데이터 | 설명 |
|--------|------|
| Core Web Vitals | LCP (최대 콘텐츠 렌더링), FID (입력 지연), CLS (누적 레이아웃 이동) |
| Navigation Timing | 페이지 로드 단계별 시간 (DNS, TCP, TTFB, DOM 로드) |
| Resource Timing | JS/CSS/이미지 등 개별 리소스 로드 시간 |
| XHR/Fetch | API 호출 지연 및 에러율 |
| JavaScript Error | 브라우저 콘솔 에러, 스택 트레이스 |
| Page View | 페이지별 방문 수, 이탈율 |

**Core Web Vitals 기준**
| 지표 | 좋음 | 개선 필요 | 나쁨 |
|------|------|-----------|------|
| LCP (로딩) | ≤ 2.5s | 2.5s~4s | > 4s |
| FID (반응성) | ≤ 100ms | 100~300ms | > 300ms |
| CLS (안정성) | ≤ 0.1 | 0.1~0.25 | > 0.25 |

### 2.2 실무 적용 코드

**Terraform — RUM App Monitor 생성**
```hcl
# Cognito Identity Pool (비인증 사용자 지표 수집용)
resource "aws_cognito_identity_pool" "rum" {
  identity_pool_name               = "rum-identity-pool"
  allow_unauthenticated_identities = true
}

resource "aws_cognito_identity_pool_roles_attachment" "rum" {
  identity_pool_id = aws_cognito_identity_pool.rum.id

  roles = {
    unauthenticated = aws_iam_role.rum_guest.arn
  }
}

resource "aws_iam_role" "rum_guest" {
  name = "rum-guest-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = "cognito-identity.amazonaws.com" }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "cognito-identity.amazonaws.com:aud" = aws_cognito_identity_pool.rum.id
        }
        "ForAnyValue:StringLike" = {
          "cognito-identity.amazonaws.com:amr" = "unauthenticated"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "rum_guest" {
  role = aws_iam_role.rum_guest.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["rum:PutRumEvents"]
      Resource = aws_rum_app_monitor.main.arn
    }]
  })
}

# RUM App Monitor
resource "aws_rum_app_monitor" "main" {
  name   = "prod-web-app"
  domain = "app.example.com"

  app_monitor_configuration {
    allow_cookies       = true
    enable_xray         = true  # X-Ray 트레이스 연동
    excluded_pages      = ["/admin", "/internal/*"]
    favorite_pages      = ["/", "/products", "/checkout"]
    session_sample_rate = 0.1  # 10% 샘플링 (트래픽 많을 때 비용 절감)
    telemetries         = ["errors", "http", "performance"]

    identity_pool_id = aws_cognito_identity_pool.rum.id
    guest_role_arn   = aws_iam_role.rum_guest.arn
  }

  custom_events {
    status = "ENABLED"
  }
}

output "rum_snippet" {
  value       = aws_rum_app_monitor.main.app_monitor_configuration
  description = "RUM JS 스니펫 삽입에 필요한 설정값"
}
```

**HTML — RUM JavaScript 스니펫 삽입**
```html
<!DOCTYPE html>
<html>
<head>
  <!-- CloudWatch RUM 스니펫 — <head> 최상단에 삽입 -->
  <script>
    (function(n,i,v,r,s,c,x,z){x=window.AwsRumClient={q:[],n:n,i:i,v:v,r:r,c:c};window[n]=function(){x.q.push(arguments)};z=document.createElement('script');z.async=true;z.src=s;document.head.insertBefore(z,document.head.getElementsByTagName('script')[0])})('cwr','MONITOR_ID','1.0.0','ap-northeast-2','https://client.rum.us-east-1.amazonaws.com/1.12.0/cwr.js',{
      sessionSampleRate: 0.1,
      identityPoolId: "ap-northeast-2:xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
      endpoint: "https://dataplane.rum.ap-northeast-2.amazonaws.com",
      telemetries: ["performance", "errors", "http"],
      allowCookies: true,
      enableXRay: true
    });
  </script>
</head>
```

**React — RUM 초기화 (SPA)**
```javascript
// src/rum.js
import { AwsRum } from 'aws-rum-web';

let awsRum;

export function initRum() {
  if (process.env.NODE_ENV !== 'production') return;

  try {
    const config = {
      sessionSampleRate: 0.1,
      identityPoolId: process.env.REACT_APP_RUM_IDENTITY_POOL_ID,
      endpoint: `https://dataplane.rum.${process.env.REACT_APP_AWS_REGION}.amazonaws.com`,
      telemetries: ['performance', 'errors', 'http'],
      allowCookies: true,
      enableXRay: true,
    };

    awsRum = new AwsRum(
      process.env.REACT_APP_RUM_APP_MONITOR_ID,
      '1.0.0',
      process.env.REACT_APP_AWS_REGION,
      config
    );
  } catch (error) {
    console.warn('RUM initialization failed:', error);
  }
}

// 커스텀 이벤트 발행
export function recordEvent(eventType, data) {
  if (awsRum) {
    awsRum.recordEvent(eventType, data);
  }
}

// src/index.js
import { initRum } from './rum';
initRum();
```

**커스텀 이벤트 — 비즈니스 지표 추적**
```javascript
import { recordEvent } from './rum';

// 주문 완료 이벤트
function onOrderComplete(orderId, amount) {
  recordEvent('com.example.order_complete', {
    orderId,
    amount,
    currency: 'KRW',
    timestamp: Date.now()
  });
}

// 검색 이벤트
function onSearch(query, resultCount) {
  recordEvent('com.example.search', {
    query,
    resultCount,
    hasResults: resultCount > 0
  });
}

// 장바구니 추가
function onAddToCart(productId, category) {
  recordEvent('com.example.add_to_cart', {
    productId,
    category
  });
}
```

**CloudWatch Logs Insights — RUM 데이터 분석**
```sql
-- 페이지별 LCP (Largest Contentful Paint) 분석
fields event_details.value, metadata.pageId
| filter event_type = "com.amazon.rum.largest_contentful_paint_event"
| stats
    avg(event_details.value) as avgLCP,
    percentile(event_details.value, 75) as p75LCP,
    count(*) as samples
  by metadata.pageId
| sort avgLCP desc

-- JavaScript 에러 빈도
fields event_details.message, event_details.filename
| filter event_type = "com.amazon.rum.js_error_event"
| stats count(*) as errorCount by event_details.message
| sort errorCount desc
| limit 20

-- API 호출 지연 분석
fields event_details.url, event_details.duration, event_details.statusCode
| filter event_type = "com.amazon.rum.http_event"
| filter event_details.statusCode >= 400 or event_details.duration > 3000
| stats
    count(*) as cnt,
    avg(event_details.duration) as avgDuration
  by event_details.url
| sort cnt desc

-- 브라우저/OS별 성능 비교
fields metadata.browserName, metadata.osName, event_details.value
| filter event_type = "com.amazon.rum.largest_contentful_paint_event"
| stats avg(event_details.value) as avgLCP by metadata.browserName, metadata.osName
| sort avgLCP desc

-- 국가별 성능 분석
fields metadata.countryCode, event_details.value
| filter event_type = "com.amazon.rum.largest_contentful_paint_event"
| stats avg(event_details.value) as avgLCP, count(*) as samples
  by metadata.countryCode
| sort avgLCP desc
```

### 2.3 보안/비용 Best Practice
- **샘플링 비율** (`sessionSampleRate`): 트래픽이 많으면 0.01(1%)~0.1(10%)으로 설정 — 비용 절감
- `excludedPages`로 관리자 페이지, 내부 도구 URL 제외
- Cognito Identity Pool은 RUM 전용으로 분리 생성 — 다른 서비스와 공유 금지
- **비용**: 수집 이벤트 100만 건당 $1 (샘플링으로 조절 가능)
- PII 포함 URL 파라미터는 `excludedPages` 패턴으로 제외하거나 커스텀 URL sanitizer 적용

## 3. 트러블슈팅
### 3.1 주요 이슈

**RUM 데이터가 수집되지 않음**
- 증상: 콘솔에서 "No data"
- 원인: CORS 오류, Cognito Identity Pool 미설정, CSP(Content Security Policy) 차단
- 해결:
  ```javascript
  // 브라우저 콘솔에서 RUM 초기화 확인
  console.log(window.AwsRumClient);  // undefined면 스니펫 미로드

  // CSP 헤더에 RUM 도메인 추가
  // Content-Security-Policy: connect-src https://dataplane.rum.ap-northeast-2.amazonaws.com
  ```

**CORS 에러**
- 원인: `endpoint` 도메인이 CSP connect-src에 미포함
- 해결:
  ```
  Content-Security-Policy: connect-src 'self'
    https://dataplane.rum.ap-northeast-2.amazonaws.com
    https://cognito-identity.ap-northeast-2.amazonaws.com
    https://sts.amazonaws.com;
  ```

### 3.2 자주 발생하는 문제 (Q&A)

- Q: SPA(React/Vue)에서 페이지 전환을 추적하려면?
- A: `recordPageView(pathname)` 메서드를 React Router의 `useEffect`나 Vue Router의 `afterEach`에 호출

- Q: 내부 직원 트래픽을 제외하려면?
- A: IP 기반 필터링은 미지원. 사내 환경에서는 `sessionSampleRate: 0`으로 설정하거나 내부 도메인 분리

- Q: X-Ray 연동 시 End-to-End 트레이스가 보이나요?
- A: `enableXRay: true` + 백엔드 X-Ray 활성화 시 브라우저 → API → DB 전체 트레이스 확인 가능

## 4. 모니터링 및 알람
```hcl
# LCP 악화 알람 (CloudWatch Metric 기반)
resource "aws_cloudwatch_metric_alarm" "lcp_poor" {
  alarm_name          = "rum-lcp-poor-experience"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "NavigationFrustratedTransaction"
  namespace           = "AWS/RUM"
  period              = 300
  statistic           = "Sum"
  threshold           = 100  # 5분간 100건 초과 시
  alarm_description   = "사용자 경험 저하 감지 (LCP > 4s)"
  alarm_actions       = [aws_sns_topic.frontend_ops.arn]
  dimensions = {
    application_name    = "prod-web-app"
    application_version = "1.0.0"
  }
}

# JavaScript 에러율 알람
resource "aws_cloudwatch_metric_alarm" "js_errors" {
  alarm_name          = "rum-js-error-spike"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "JsErrorCount"
  namespace           = "AWS/RUM"
  period              = 300
  statistic           = "Sum"
  threshold           = 50
  alarm_description   = "JavaScript 에러 급증"
  alarm_actions       = [aws_sns_topic.frontend_ops.arn]
  dimensions = {
    application_name = "prod-web-app"
  }
}
```

## 5. TIP
- **배포 후 검증**: 새 배포 직후 RUM 대시보드를 모니터링해 성능 회귀 조기 감지
- `favoritePages` 설정으로 주요 전환 페이지(홈, 상품, 결제)를 별도 추적 — 비즈니스 임팩트 높은 페이지 집중 관리
- 커스텀 이벤트와 Logs Insights를 조합하면 "검색 후 구매까지 걸린 시간" 같은 사용자 여정 분석 가능
- CloudWatch ServiceLens와 연동하면 프론트엔드(RUM) → 백엔드(X-Ray) → 인프라 통합 관찰성 구현
- 관련 문서: [CloudWatch RUM 공식 가이드](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-RUM.html)
