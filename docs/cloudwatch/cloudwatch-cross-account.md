# CloudWatch 크로스 계정 관찰성 (Cross-Account Observability)

## 1. 개요
- 여러 AWS 계정(멀티 계정 환경)의 지표, 로그, 트레이스를 중앙 모니터링 계정 하나에서 통합 조회하는 기능
- 2022년 출시된 **CloudWatch Observability Access Manager (OAM)** 기반 — 기존 크로스 계정 방식(CloudWatch API 크로스 계정 역할)을 대체
- DevOps/SRE 팀이 서비스 계정마다 로그인할 필요 없이 단일 대시보드에서 전체 환경 관찰 가능

## 2. 설명
### 2.1 핵심 개념

**구성 요소**
| 역할 | 계정 유형 | 설명 |
|------|-----------|------|
| Monitoring Account | 중앙 모니터링 계정 | 대시보드, 알람, 분석 수행 |
| Source Account | 소스 계정 | 실제 워크로드가 동작하는 계정 |
| Sink | OAM Sink | Monitoring Account에 생성, 소스 계정이 연결 대상 |
| Link | OAM Link | Source Account에 생성, Sink에 연결 |

**지원 데이터 유형**
- CloudWatch Metrics
- CloudWatch Logs
- X-Ray Traces
- Application Insights 애플리케이션

**아키텍처**
```
[Source Account A] ──Link──┐
[Source Account B] ──Link──┼──> [Sink] ──> [Monitoring Account]
[Source Account C] ──Link──┘              (통합 대시보드/알람)
```

### 2.2 실무 적용 코드

**Terraform — Monitoring Account (Sink 생성)**
```hcl
# Monitoring Account에 OAM Sink 생성
resource "aws_oam_sink" "main" {
  name = "central-monitoring-sink"

  tags = {
    Environment = "monitoring"
    ManagedBy   = "terraform"
  }
}

# Sink Policy — 어떤 계정/OU가 연결 가능한지 정의
resource "aws_oam_sink_policy" "main" {
  sink_identifier = aws_oam_sink.main.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action   = ["oam:CreateLink", "oam:UpdateLink"]
        Effect   = "Allow"
        Resource = "*"
        Principal = {
          AWS = [
            # 특정 계정만 허용
            "arn:aws:iam::111111111111:root",  # prod 계정
            "arn:aws:iam::222222222222:root",  # staging 계정
            "arn:aws:iam::333333333333:root",  # dev 계정
          ]
        }
        Condition = {
          StringEquals = {
            "aws:PrincipalOrgID" = "o-xxxxxxxxxxxx"  # 또는 Organizations ID로 제한
          }
        }
      }
    ]
  })
}

output "sink_arn" {
  value = aws_oam_sink.main.arn
}
```

**Terraform — Source Account (Link 생성)**
```hcl
# Source Account에서 Monitoring Account Sink에 Link 생성
variable "monitoring_sink_arn" {
  description = "Monitoring Account의 OAM Sink ARN"
  type        = string
}

resource "aws_oam_link" "to_monitoring" {
  label_template  = "$AccountName"  # 소스 계정 레이블 (대시보드에서 구분용)
  resource_types  = [
    "AWS::CloudWatch::Metric",
    "AWS::Logs::LogGroup",
    "AWS::XRay::Trace"
  ]
  sink_identifier = var.monitoring_sink_arn

  tags = {
    Environment = "prod"
    LinkedTo    = "monitoring"
  }
}
```

**AWS CLI — Monitoring Account 설정**
```bash
# Sink 생성
SINK_ARN=$(aws oam create-sink \
  --name "central-monitoring-sink" \
  --query 'Arn' \
  --output text)

echo "Sink ARN: $SINK_ARN"

# Sink Policy 설정 (특정 계정 허용)
aws oam put-sink-policy \
  --sink-identifier "$SINK_ARN" \
  --policy '{
    "Version": "2012-10-17",
    "Statement": [{
      "Action": ["oam:CreateLink", "oam:UpdateLink"],
      "Effect": "Allow",
      "Resource": "*",
      "Principal": {
        "AWS": [
          "arn:aws:iam::111111111111:root",
          "arn:aws:iam::222222222222:root"
        ]
      }
    }]
  }'

# 연결된 Link 목록 확인
aws oam list-attached-links --sink-identifier "$SINK_ARN"
```

**AWS CLI — Source Account 설정**
```bash
# Source Account에서 Sink에 Link 생성
aws oam create-link \
  --label-template '$AccountName' \
  --resource-types \
    "AWS::CloudWatch::Metric" \
    "AWS::Logs::LogGroup" \
    "AWS::XRay::Trace" \
  --sink-identifier "arn:aws:oam:ap-northeast-2:999999999999:sink/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"

# Link 상태 확인
aws oam list-links
```

**Organizations 수준 자동화 — CloudFormation StackSet**
```yaml
# monitoring-link-stackset.yaml
AWSTemplateFormatVersion: '2010-09-09'
Description: 'OAM Link to Central Monitoring Account'

Parameters:
  MonitoringSinkArn:
    Type: String
    Description: ARN of the OAM Sink in the Monitoring Account

Resources:
  ObservabilityLink:
    Type: AWS::Oam::Link
    Properties:
      LabelTemplate: "$AccountName"
      ResourceTypes:
        - AWS::CloudWatch::Metric
        - AWS::Logs::LogGroup
        - AWS::XRay::Trace
      SinkIdentifier: !Ref MonitoringSinkArn
      Tags:
        ManagedBy: StackSet
        LinkedTo: central-monitoring
```

```bash
# Organizations 전체에 StackSet 배포
aws cloudformation create-stack-set \
  --stack-set-name "oam-monitoring-link" \
  --template-body file://monitoring-link-stackset.yaml \
  --parameters ParameterKey=MonitoringSinkArn,ParameterValue="arn:aws:oam:ap-northeast-2:999999999999:sink/xxxx" \
  --permission-model SERVICE_MANAGED \
  --auto-deployment Enabled=true,RetainStacksOnAccountRemoval=false

# OU 단위 배포
aws cloudformation create-stack-instances \
  --stack-set-name "oam-monitoring-link" \
  --deployment-targets OrganizationalUnitIds=["ou-xxxx-xxxxxxxx"] \
  --regions ap-northeast-2
```

**Monitoring Account — 크로스 계정 지표 쿼리**
```bash
# 연결된 소스 계정의 지표 조회
aws cloudwatch list-metrics \
  --namespace "AWS/EC2" \
  --include-linked-accounts  # 이 플래그로 소스 계정 지표 포함

# 크로스 계정 지표 데이터 조회
aws cloudwatch get-metric-data \
  --metric-data-queries '[
    {
      "Id": "m1",
      "MetricStat": {
        "Metric": {
          "Namespace": "AWS/ApplicationELB",
          "MetricName": "RequestCount",
          "Dimensions": [
            {"Name": "LoadBalancer", "Value": "app/prod-alb/abc123"},
            {"Name": "aws.AccountId", "Value": "111111111111"}
          ]
        },
        "Period": 60,
        "Stat": "Sum"
      },
      "AccountId": "111111111111"
    }
  ]' \
  --start-time 2024-01-01T00:00:00Z \
  --end-time 2024-01-01T01:00:00Z

# 크로스 계정 로그 검색
aws logs filter-log-events \
  --log-group-name "/aws/eks/prod-cluster/application" \
  --account-id "111111111111" \
  --filter-pattern "ERROR"
```

**Terraform — 크로스 계정 대시보드**
```hcl
resource "aws_cloudwatch_dashboard" "multi_account" {
  dashboard_name = "multi-account-overview"

  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric"
        properties = {
          title  = "전체 계정 ALB 요청 수"
          region = "ap-northeast-2"
          metrics = [
            # 각 소스 계정의 지표를 AccountId 차원으로 구분
            ["AWS/ApplicationELB", "RequestCount",
              "LoadBalancer", "app/prod-alb/aaa", "aws.AccountId", "111111111111",
              { "label": "Prod Account" }],
            ["AWS/ApplicationELB", "RequestCount",
              "LoadBalancer", "app/staging-alb/bbb", "aws.AccountId", "222222222222",
              { "label": "Staging Account" }]
          ]
          view   = "timeSeries"
          period = 60
          stat   = "Sum"
        }
      },
      {
        type = "log"
        properties = {
          title  = "전체 계정 에러 로그"
          region = "ap-northeast-2"
          query  = "SOURCE ACCOUNT '111111111111' '/aws/eks/prod-cluster/application' | filter @message like /ERROR/ | stats count(*) by bin(5m)"
        }
      }
    ]
  })
}
```

### 2.3 보안/비용 Best Practice
- **최소 권한 Sink Policy**: Organizations ID 또는 특정 계정 ARN으로 제한 — 와일드카드(`*`) 사용 금지
- Monitoring Account IAM 역할은 읽기 전용으로 분리 (알람 생성은 가능, 소스 계정 리소스 변경 불가)
- 크로스 계정 로그 조회 시 PII 포함 로그 그룹은 Link에서 제외하거나 별도 암호화 키 적용
- **비용**: OAM 자체 추가 비용 없음. 크로스 계정 지표/로그 조회 시 API 호출 비용 발생

## 3. 트러블슈팅
### 3.1 주요 이슈

**Link 생성 실패 (AccessDenied)**
- 증상: `oam:CreateLink` 권한 오류
- 원인: Sink Policy에 소스 계정 ARN 미포함
- 해결:
  ```bash
  # Sink Policy 확인
  aws oam get-sink-policy \
    --sink-identifier "arn:aws:oam:ap-northeast-2:999999999999:sink/xxxx"

  # 소스 계정 ARN 추가 후 Policy 업데이트
  aws oam put-sink-policy \
    --sink-identifier "arn:aws:oam:ap-northeast-2:999999999999:sink/xxxx" \
    --policy '{ ... "Principal": {"AWS": ["arn:aws:iam::NEW_ACCOUNT_ID:root"]} ... }'
  ```

**크로스 계정 지표가 대시보드에 안 보임**
- 원인: `--include-linked-accounts` 플래그 미사용 또는 리전 불일치
- 해결: Monitoring Account와 Source Account의 Link가 같은 리전인지 확인
  ```bash
  aws oam list-links --region ap-northeast-2
  ```

### 3.2 자주 발생하는 문제 (Q&A)

- Q: 리전 간 크로스 계정 지표를 볼 수 있나요?
- A: Link는 리전 단위. 멀티 리전 모니터링은 각 리전에 Sink/Link 별도 생성 필요

- Q: OAM과 기존 CloudWatch 크로스 계정 역할 방식의 차이?
- A: 기존 방식은 IAM Role Assume 기반이라 계정마다 역할 설정 복잡. OAM은 데이터 플레인에서 직접 공유하므로 설정 단순

- Q: 연결된 소스 계정에서 알람을 생성할 수 있나요?
- A: Monitoring Account에서 소스 계정 지표 기반으로 알람 생성 가능 (크로스 계정 알람)

## 4. 모니터링 및 알람
```bash
# OAM Link 상태 모니터링 — EventBridge
aws events put-rule \
  --name "oam-link-status-change" \
  --event-pattern '{
    "source": ["aws.oam"],
    "detail-type": ["CloudWatch Observability Access Manager Link Status Changed"]
  }' \
  --state ENABLED

# 연결된 소스 계정 수 확인
aws oam list-attached-links \
  --sink-identifier "arn:aws:oam:ap-northeast-2:999999999999:sink/xxxx" \
  --query 'length(Items)'
```

## 5. TIP
- **AWS Organizations 연동**: Control Tower 또는 Organizations StackSet으로 새 계정 생성 시 자동으로 Link 생성
- `$AccountName` 레이블 템플릿은 계정 별칭(Account Alias)을 사용 — AWS Organizations의 계정 이름이 아님
- 크로스 계정 Logs Insights에서 `SOURCE ACCOUNT 'ACCOUNT_ID' 'LOG_GROUP'` 구문으로 특정 계정 로그 쿼리 가능
- CloudWatch 통합 대시보드에서 계정 전환 없이 "Account" 드롭다운으로 필터링 가능
- 관련 문서: [OAM 설정 가이드](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-Unified-Cross-Account.html)
