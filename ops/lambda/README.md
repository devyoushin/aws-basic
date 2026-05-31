# AWS Lambda 실무 예제 모음

실무에서 자주 사용하는 Lambda 패턴을 Python으로 구현한 예제 모음입니다.

## 구성

| 폴더 | 기능 | 트리거 | 주요 패턴 |
|------|------|--------|-----------|
| `ec2_scheduler/` | EC2 자동 시작/중지 | EventBridge Cron | 태그 기반 필터, DRY_RUN |
| `slack_alarm_notifier/` | CloudWatch Alarm → Slack | SNS | Slack Block Kit, Secrets Manager |
| `s3_event_processor/` | S3 업로드 파일 후처리 | S3 Event | CSV 파싱, JSON Logs 분석, S3 Select |
| `ebs_snapshot_cleanup/` | EBS 스냅샷 자동 정리 | EventBridge Cron | AMI 연결 스냅샷 보호, 안전장치 |
| `cost_anomaly_alert/` | 비용 급등 탐지 → Slack | EventBridge Cron | Cost Explorer, 전일 대비 비교 |
| `sqs_batch_processor/` | SQS 메시지 배치 처리 | SQS | Partial Batch Failure 패턴 |
| `rds_snapshot_manager/` | RDS/Aurora 스냅샷 자동화 | EventBridge Cron | 크로스 리전 복사, 보존 기간 관리 |
| `secrets_rotation/` | DB 암호 자동 교체 | Secrets Manager | 4단계 교체 프로세스, MySQL/PostgreSQL |

---

## 공통 패턴

### 1. DRY_RUN 환경 변수
대부분의 Lambda에 `DRY_RUN=true` 환경 변수를 지원합니다.
실제 변경 없이 어떤 리소스가 영향받는지 로그로 확인할 수 있습니다.

```bash
# 테스트 실행 (실제 삭제/시작/중지 없음)
aws lambda invoke \
  --function-name ebs-snapshot-cleanup \
  --payload '{}' \
  --environment-override '{"Variables": {"DRY_RUN": "true"}}' \
  response.json
```

### 2. SNS 알림
`SNS_TOPIC_ARN` 환경 변수를 설정하면 처리 결과를 SNS로 발송합니다.
SNS → Email, Slack, PagerDuty 등으로 연결 가능합니다.

### 3. 멱등성 (Idempotency)
Lambda는 재시도가 발생할 수 있으므로 모든 예제는 멱등성을 고려했습니다.
- 이미 존재하는 스냅샷 → 건너뜀
- 이미 중지된 인스턴스 → 건너뜀
- SQS Partial Batch Failure → 실패한 메시지만 재처리

---

## 배포 방법

### 기본 배포 (boto3만 사용하는 경우)
```bash
# Lambda 패키지 생성
cd lambda/ec2_scheduler
zip -r function.zip lambda_function.py

# 배포
aws lambda create-function \
  --function-name ec2-scheduler \
  --runtime python3.12 \
  --handler lambda_function.lambda_handler \
  --role arn:aws:iam::ACCOUNT_ID:role/LambdaExecutionRole \
  --zip-file fileb://function.zip \
  --timeout 300 \
  --memory-size 256 \
  --environment Variables="{ACTION=stop,TAG_KEY=AutoSchedule,TAG_VALUE=true}"
```

### 외부 패키지 포함 배포 (secrets_rotation 등)
```bash
cd lambda/secrets_rotation

# 패키지 설치
pip install -r requirements.txt -t ./package

# 패키지 + 코드 압축
cp lambda_function.py ./package/
cd package && zip -r ../function.zip . && cd ..

# 배포
aws lambda update-function-code \
  --function-name rds-secrets-rotation \
  --zip-file fileb://function.zip
```

### EventBridge Cron 연결 예시 (EC2 Scheduler)
```bash
# 평일 오후 9시(KST = UTC 12시) 자동 중지
aws events put-rule \
  --name "ec2-auto-stop" \
  --schedule-expression "cron(0 12 ? * MON-FRI *)" \
  --state ENABLED

aws events put-targets \
  --rule "ec2-auto-stop" \
  --targets "Id=ec2-scheduler,Arn=arn:aws:lambda:ap-northeast-2:ACCOUNT:function:ec2-scheduler"
```

---

## 환경 변수 요약

| Lambda | 환경 변수 | 설명 |
|--------|-----------|------|
| ec2_scheduler | `ACTION` | start \| stop |
| ec2_scheduler | `TAG_KEY` / `TAG_VALUE` | 대상 태그 |
| slack_alarm_notifier | `SLACK_WEBHOOK_URL` | Slack Webhook |
| s3_event_processor | `DEST_BUCKET` / `DEST_PREFIX` | 처리 완료 이동 경로 |
| s3_event_processor | `DYNAMODB_TABLE` | CSV → DynamoDB 저장 |
| ebs_snapshot_cleanup | `RETENTION_DAYS` | 스냅샷 보존 기간 |
| cost_anomaly_alert | `THRESHOLD_PCT` | 급등 기준 (기본 50%) |
| cost_anomaly_alert | `SLACK_WEBHOOK_URL` | Slack Webhook |
| sqs_batch_processor | `PROCESSING_TYPE` | dynamodb \| s3 \| http \| log |
| rds_snapshot_manager | `ACTION` | create \| copy \| cleanup \| all |
| rds_snapshot_manager | `DB_IDENTIFIERS` | 쉼표 구분 DB 식별자 |
| rds_snapshot_manager | `COPY_REGION` | 크로스 리전 복사 대상 |
| secrets_rotation | `PASSWORD_LENGTH` | 암호 길이 (기본 32) |

---

## 권장 IAM 역할 구성

각 Lambda 파일 상단 docstring에 필요한 IAM 권한이 명시되어 있습니다.
최소 권한 원칙에 따라 Lambda별로 별도 IAM Role을 생성하세요.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:*:*:*"
    }
  ]
}
```
