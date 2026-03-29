# CloudWatch Synthetics

## 1. 개요
- CloudWatch Synthetics는 실제 사용자 트래픽 없이도 API 엔드포인트, UI 플로우를 주기적으로 테스트하는 외부 모니터링 서비스
- Lambda 기반 **Canary** 스크립트를 실행해 가용성, 지연시간, 정확성을 측정하고 알람으로 연동
- 내부 CloudWatch 지표만으로는 알 수 없는 "실제 사용자 관점의 서비스 상태"를 파악할 수 있어 SLA 측정에 핵심

## 2. 설명
### 2.1 핵심 개념

**Canary 유형**
| 유형 | 용도 | 예시 |
|------|------|------|
| API Canary | REST/GraphQL API 엔드포인트 응답 검증 | 헬스체크, 인증 플로우 |
| GUI Workflow | 실제 브라우저로 UI 시나리오 실행 (Puppeteer 기반) | 로그인 → 주문 → 결제 |
| Heartbeat Monitor | 단순 URL 가용성 및 응답시간 측정 | 홈페이지 200 OK 확인 |
| Broken Link Checker | 웹페이지 내 링크 유효성 검사 | |
| Visual Monitoring | 스크린샷 비교로 UI 변경 감지 | |

**런타임 환경**
- Node.js (syn-nodejs-puppeteer-x.x)
- Python (syn-python-selenium-x.x)
- Puppeteer: 헤드리스 Chrome 기반 — UI 테스트에 사용

**결과 저장**
- HAR 파일, 스크린샷, 로그 → S3 자동 저장
- CloudWatch 지표: `SuccessPercent`, `Duration`
- CloudWatch Logs: 실행 로그 (`/aws/synthetics/canary/<name>`)

### 2.2 실무 적용 코드

**API Canary — REST API 헬스체크 (Node.js)**
```javascript
// synthetics-api-check.js
const synthetics = require('Synthetics');
const log = require('SyntheticsLogger');
const syntheticsConfiguration = synthetics.getConfiguration();

const apiCanaryBlueprint = async function () {
  syntheticsConfiguration.setConfig({
    restrictedHeaders: [],
    restrictedUrlParameters: []
  });

  // 기본 헬스체크
  const stepConfig = {
    includeRequestHeaders: true,
    includeResponseHeaders: true,
    includeRequestBody: true,
    includeResponseBody: true,
    restrictedHeaders: ['Authorization'],
    continueOnStepFailure: false
  };

  // Step 1: 헬스체크 엔드포인트
  await synthetics.executeHttpStep(
    'Health Check',
    {
      hostname: 'api.example.com',
      method: 'GET',
      path: '/health',
      protocol: 'https:',
      port: 443,
      headers: {
        'User-Agent': 'CloudWatchSynthetics'
      }
    },
    async function (res) {
      if (res.statusCode !== 200) {
        throw new Error(`Health check failed: ${res.statusCode}`);
      }

      let body = '';
      await new Promise((resolve) => {
        res.on('data', (chunk) => { body += chunk; });
        res.on('end', resolve);
      });

      const responseBody = JSON.parse(body);
      if (responseBody.status !== 'healthy') {
        throw new Error(`Service unhealthy: ${JSON.stringify(responseBody)}`);
      }

      log.info(`Health check passed: ${body}`);
    },
    stepConfig
  );

  // Step 2: 인증 API 확인
  await synthetics.executeHttpStep(
    'Auth Token Validation',
    {
      hostname: 'api.example.com',
      method: 'POST',
      path: '/api/v1/token/validate',
      protocol: 'https:',
      port: 443,
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${process.env.API_TOKEN}`
      },
      body: JSON.stringify({ test: true })
    },
    async function (res) {
      if (res.statusCode !== 200) {
        throw new Error(`Auth validation failed: ${res.statusCode}`);
      }
    },
    stepConfig
  );
};

exports.handler = async () => {
  return await apiCanaryBlueprint();
};
```

**GUI Workflow Canary — 로그인 플로우 (Puppeteer)**
```javascript
// synthetics-login-flow.js
const synthetics = require('Synthetics');
const log = require('SyntheticsLogger');

const loginFlow = async function () {
  let page = await synthetics.getPage();

  // 페이지 로드 타임아웃 설정
  await page.setDefaultTimeout(30000);

  // Step 1: 로그인 페이지 접근
  await synthetics.executeStep('Navigate to Login', async function () {
    await page.goto('https://app.example.com/login', {
      waitUntil: 'networkidle2',
      timeout: 30000
    });
    await synthetics.takeScreenshot('login-page', 'loaded');
  });

  // Step 2: 자격증명 입력
  await synthetics.executeStep('Enter Credentials', async function () {
    await page.type('#email', process.env.TEST_USERNAME);
    await page.type('#password', process.env.TEST_PASSWORD);
    await synthetics.takeScreenshot('credentials', 'entered');
  });

  // Step 3: 로그인 제출
  await synthetics.executeStep('Submit Login', async function () {
    await Promise.all([
      page.waitForNavigation({ waitUntil: 'networkidle2' }),
      page.click('#login-button')
    ]);
    await synthetics.takeScreenshot('after-login', 'navigated');
  });

  // Step 4: 로그인 성공 검증
  await synthetics.executeStep('Verify Dashboard', async function () {
    const url = page.url();
    if (!url.includes('/dashboard')) {
      const errorMsg = await page.$eval('.error-message', el => el.textContent).catch(() => 'No error message');
      throw new Error(`Login failed. Current URL: ${url}, Error: ${errorMsg}`);
    }

    await page.waitForSelector('.dashboard-content', { timeout: 10000 });
    log.info('Login flow completed successfully');
  });
};

exports.handler = async () => {
  return await loginFlow();
};
```

**Terraform — Canary 생성**
```hcl
# S3 버킷 (결과 저장)
resource "aws_s3_bucket" "canary_results" {
  bucket = "prod-canary-results-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_lifecycle_configuration" "canary_results" {
  bucket = aws_s3_bucket.canary_results.id

  rule {
    id     = "delete-old-results"
    status = "Enabled"

    expiration {
      days = 30
    }
  }
}

# IAM Role
resource "aws_iam_role" "canary" {
  name = "cloudwatch-synthetics-canary-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "canary" {
  role = aws_iam_role.canary.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetBucketLocation"
        ]
        Resource = [
          aws_s3_bucket.canary_results.arn,
          "${aws_s3_bucket.canary_results.arn}/*"
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["cloudwatch:PutMetricData"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Effect   = "Allow"
        Action   = ["xray:PutTraceSegments"]
        Resource = "*"
      }
    ]
  })
}

# Canary 스크립트 패키징
data "archive_file" "canary_script" {
  type        = "zip"
  output_path = "${path.module}/canary.zip"

  source {
    content  = file("${path.module}/scripts/synthetics-api-check.js")
    filename = "nodejs/node_modules/synthetics-api-check.js"
  }
}

# Canary 리소스
resource "aws_synthetics_canary" "api_health" {
  name                 = "prod-api-health"
  artifact_s3_location = "s3://${aws_s3_bucket.canary_results.id}/api-health/"
  execution_role_arn   = aws_iam_role.canary.arn
  handler              = "synthetics-api-check.handler"
  zip_file             = data.archive_file.canary_script.output_base64sha256
  runtime_version      = "syn-nodejs-puppeteer-7.0"
  start_canary         = true

  schedule {
    expression          = "rate(5 minutes)"  # 5분마다 실행
    duration_in_seconds = 0                  # 무기한 실행
  }

  run_config {
    timeout_in_seconds = 60
    memory_in_mb       = 960
    active_tracing     = true  # X-Ray 트레이싱

    environment_variables = {
      API_TOKEN = aws_ssm_parameter.api_token.value
    }
  }

  success_retention_period = 7   # 성공 결과 7일 보관
  failure_retention_period = 30  # 실패 결과 30일 보관

  tags = {
    Environment = "prod"
    Team        = "ops"
  }
}

# Canary 알람
resource "aws_cloudwatch_metric_alarm" "canary_failure" {
  alarm_name          = "prod-api-canary-failure"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  metric_name         = "SuccessPercent"
  namespace           = "CloudWatchSynthetics"
  period              = 300
  statistic           = "Average"
  threshold           = 100  # 100% 성공 기대 (1번이라도 실패 시 알람)
  alarm_description   = "프로덕션 API Canary 실패 감지"
  alarm_actions       = [aws_sns_topic.oncall.arn]
  treat_missing_data  = "breaching"  # 데이터 없으면 알람

  dimensions = {
    CanaryName = aws_synthetics_canary.api_health.name
  }
}
```

**AWS CLI — Canary 상태 확인**
```bash
# 모든 Canary 목록
aws synthetics describe-canaries --query 'Canaries[*].{Name:Name,Status:Status.State}'

# 최근 실행 결과
aws synthetics get-canary-runs \
  --name prod-api-health \
  --query 'CanaryRuns[0:5].{Status:Status.State,Start:Timeline.Started,Duration:Timeline.Completed}'

# 특정 실행 결과 상세 조회
aws synthetics get-canary-runs \
  --name prod-api-health \
  --query 'CanaryRuns[?Status.State==`FAILED`]'
```

### 2.3 보안/비용 Best Practice
- **테스트 자격증명**: 전용 테스트 계정/토큰 사용 — 프로덕션 관리자 자격증명 절대 금지
- 민감 정보는 `environment_variables` 대신 Secrets Manager → Lambda 환경변수로 주입
- VPC 내 프라이빗 API 테스트 시 Canary를 VPC에 배치 (subnet_id 설정)
- **비용**: Canary 실행당 $0.0012 (5분 간격, 월 약 8,640회 = 약 $10/월)
- 실패 결과 스크린샷/HAR 파일에 민감 정보가 포함될 수 있으므로 S3 버킷 암호화 필수

## 3. 트러블슈팅
### 3.1 주요 이슈

**Canary가 항상 FAILED 상태**
- 증상: 스크립트 문법 오류 없이 실패
- 원인: 네트워크 접근 불가 (VPC 설정 누락), 타임아웃, IAM 권한 부족
- 해결:
  ```bash
  # CloudWatch Logs에서 에러 확인
  aws logs filter-log-events \
    --log-group-name /aws/synthetics/canary/prod-api-health \
    --filter-pattern "ERROR" \
    --limit 20

  # S3에서 실패 결과(HAR, 스크린샷) 확인
  aws s3 ls s3://prod-canary-results/api-health/ --recursive | grep FAILED
  ```

**Puppeteer 타임아웃**
- 증상: GUI Canary가 특정 단계에서 30초 타임아웃
- 원인: 페이지 렌더링 지연 또는 element selector 변경
- 해결:
  - `page.waitForSelector(selector, { timeout: 10000 })` 타임아웃 조정
  - `{ waitUntil: 'networkidle2' }` 대신 `'domcontentloaded'` 시도
  - 스크린샷으로 현재 페이지 상태 확인 후 selector 수정

### 3.2 자주 발생하는 문제 (Q&A)

- Q: 프라이빗 VPC 내 서비스를 테스트하려면?
- A: Canary의 `vpc_config` 설정에 subnet_id와 security_group_id 지정. NAT Gateway 또는 VPC Endpoint 필요

- Q: Canary 스크립트를 로컬에서 테스트할 수 있나요?
- A: `@aws-sdk/client-synthetics-runtime` npm 패키지로 로컬 테스트 가능. 단 Puppeteer 환경 차이 주의

- Q: 여러 리전에서 동시 테스트하려면?
- A: 각 리전에 Canary를 별도 배포. Route 53 헬스체크와 연동하면 Failover 자동화 가능

## 4. 모니터링 및 알람
```hcl
# Canary 지속시간 이상 감지 (응답시간 악화 탐지)
resource "aws_cloudwatch_metric_alarm" "canary_slow" {
  alarm_name          = "prod-api-canary-slow"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "Duration"
  namespace           = "CloudWatchSynthetics"
  period              = 300
  statistic           = "Average"
  threshold           = 5000  # 5초 초과 시 알람 (ms 단위)
  alarm_description   = "API Canary 응답시간 5초 초과"
  alarm_actions       = [aws_sns_topic.warning.arn]
  dimensions = {
    CanaryName = aws_synthetics_canary.api_health.name
  }
}
```

## 5. TIP
- **Canary 재사용 패턴**: 공통 헬퍼(인증, 요청 래퍼)를 Lambda Layer로 분리해 여러 Canary에서 공유
- `active_tracing = true`로 X-Ray 활성화 시 외부 → ALB → API → DB 전체 추적 경로 확인 가능
- 배포 후 Blue/Green 검증 자동화: CodePipeline에서 Canary SuccessPercent를 배포 게이트로 활용
- 관련 문서: [Synthetics Canary 런타임 버전](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Synthetics_Library_nodejs_puppeteer.html)
