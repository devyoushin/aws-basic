# AWS 비용 최적화 전략

## 1. 개요

AWS 비용은 인지하지 못하는 사이에 빠르게 증가한다.
Savings Plans, Reserved Instance(RI), Spot 인스턴스를 적절히 조합하고,
스토리지·네트워크·미사용 리소스를 정기적으로 점검하면 30~60% 절감이 가능하다.
비용 최적화는 일회성이 아닌 지속적인 FinOps 문화로 정착시켜야 효과적이다.

---

## 2. 설명

### 2.1 핵심 개념

**구매 옵션 비교 (EC2 기준)**

| 옵션 | 할인율 | 조건 | 권장 워크로드 |
|------|--------|------|------------|
| On-Demand | 기준(0%) | 없음 | 예측 불가 단기 |
| Savings Plans (Compute) | 최대 66% | 1/3년 약정, 시간당 $ 약정 | EC2+Lambda+Fargate 유연 적용 |
| Savings Plans (EC2) | 최대 72% | 1/3년, 특정 패밀리/리전 약정 | 안정적인 단일 패밀리 |
| Reserved Instance | 최대 72% | 1/3년, 특정 인스턴스 타입 | 예측 가능한 DB, 고정 워크로드 |
| Spot Instance | 최대 90% | 중단 가능 | 배치, CI/CD, 비동기 처리 |

**Savings Plans vs Reserved Instance 선택 기준**

```
Savings Plans (Compute) 선택:
  - 인스턴스 타입 변경 가능성 있음
  - EC2 + Lambda + Fargate 혼합 사용
  - 리전 이동 가능성 있음
  → 유연성 최우선, 약정 금액($)만 지정

Reserved Instance 선택:
  - 3년간 동일 인스턴스 타입 확정 (RDS, ElastiCache)
  - 특정 AZ 용량 예약 필요 (Zonal RI)
  → 최대 할인이 필요하고 변경 가능성 없음
```

**비용 구조 이해**

```
AWS 비용 = 컴퓨팅 + 스토리지 + 네트워크 + 관리형 서비스

컴퓨팅: EC2, ECS/Fargate, Lambda, EKS 노드
스토리지: EBS, S3, EFS, RDS 스토리지
네트워크: 데이터 전송(egress), NAT GW 처리 요금, VPC 엔드포인트
관리형: RDS, ElastiCache, MSK 등 인스턴스 시간 요금
```

---

### 2.2 실무 적용 코드

**Savings Plans 구매 전 분석 (Cost Explorer)**

```bash
# 지난 7일 On-Demand 사용량 확인 → Savings Plans 추천
aws ce get-savings-plans-purchase-recommendation \
  --savings-plans-type COMPUTE_SP \
  --term-in-years ONE_YEAR \
  --payment-option NO_UPFRONT \
  --lookback-period-in-days SEVEN_DAYS \
  --query 'SavingsPlansPurchaseRecommendation.{
    EstimatedMonthlySavings:SavingsPlansPurchaseRecommendationSummary.EstimatedMonthlySavingsAmount,
    Recommendation:SavingsPlansPurchaseRecommendationDetails[0]
  }'
```

**미사용 리소스 탐지 (AWS CLI)**

```bash
# 미사용 EBS 볼륨 (EC2에 연결 안 된 볼륨)
aws ec2 describe-volumes \
  --filters "Name=status,Values=available" \
  --query 'Volumes[*].{ID:VolumeId,Size:Size,Type:VolumeType,AZ:AvailabilityZone}' \
  --output table

# 미사용 Elastic IP
aws ec2 describe-addresses \
  --query 'Addresses[?AssociationId==null].{IP:PublicIp,AllocationId:AllocationId}' \
  --output table

# 오래된 스냅샷 (180일 이상)
aws ec2 describe-snapshots \
  --owner-ids self \
  --query "Snapshots[?StartTime<='$(date -d '180 days ago' +%Y-%m-%d)'].{ID:SnapshotId,Size:VolumeSize,Date:StartTime}" \
  --output table

# 미사용 Load Balancer (타겟 없는 ALB)
aws elbv2 describe-target-groups \
  --query 'TargetGroups[?TargetType!=`lambda`].{ARN:TargetGroupArn,LBArn:LoadBalancerArns[0]}' | \
  jq '.[] | select(.LBArn == null)'

# 중지된 EC2 인스턴스 (EBS 비용은 계속 발생)
aws ec2 describe-instances \
  --filters "Name=instance-state-name,Values=stopped" \
  --query 'Reservations[*].Instances[*].{ID:InstanceId,Type:InstanceType,Stop:StateTransitionReason}' \
  --output table
```

**EBS gp2 → gp3 전환 (20% 비용 절감)**

```bash
# 계정 내 모든 gp2 볼륨 찾기
aws ec2 describe-volumes \
  --filters "Name=volume-type,Values=gp2" \
  --query 'Volumes[*].{ID:VolumeId,Size:Size,IOPS:Iops}' \
  --output table

# gp3로 변환 (무중단)
aws ec2 modify-volume \
  --volume-id vol-xxxxxxxx \
  --volume-type gp3 \
  --iops 3000 \
  --throughput 125
# gp2 IOPS = size * 3 (버스트 포함). gp3는 3000 IOPS 기본 무료
```

**Terraform — 비용 절감 자동화**

```hcl
# 개발 환경 EC2 스케줄 ON/OFF (업무 시간만 운영)
resource "aws_autoscaling_schedule" "dev_off" {
  scheduled_action_name  = "dev-night-scale-down"
  autoscaling_group_name = aws_autoscaling_group.dev.name
  recurrence             = "0 12 * * MON-FRI"   # UTC 21:00 KST (퇴근 후)
  min_size               = 0
  max_size               = 0
  desired_capacity       = 0
}

resource "aws_autoscaling_schedule" "dev_on" {
  scheduled_action_name  = "dev-morning-scale-up"
  autoscaling_group_name = aws_autoscaling_group.dev.name
  recurrence             = "0 0 * * MON-FRI"    # UTC 09:00 KST (출근)
  min_size               = 1
  max_size               = 3
  desired_capacity       = 1
}

# RDS 개발 환경 스케줄 중지 (Aurora Serverless v2 제외)
resource "aws_rds_cluster" "dev" {
  # ...

  # 8시간 이상 미사용 시 자동 중지 (Serverless만 지원)
  scaling_configuration {
    auto_pause               = true
    min_capacity             = 1
    max_capacity             = 4
    seconds_until_auto_pause = 28800   # 8시간
  }
}

# S3 Intelligent-Tiering (자동 스토리지 클래스 최적화)
resource "aws_s3_bucket_intelligent_tiering_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  name   = "entire-bucket"

  tiering {
    access_tier = "ARCHIVE_ACCESS"
    days        = 90
  }

  tiering {
    access_tier = "DEEP_ARCHIVE_ACCESS"
    days        = 180
  }
}
```

**Lambda & Fargate 비용 최적화**

```hcl
# Lambda — ARM64(Graviton)로 전환 (같은 성능에 20% 저렴)
resource "aws_lambda_function" "api" {
  # ...
  architectures = ["arm64"]
  memory_size   = 512   # 실제 사용량 확인 후 최소화

  # Provisioned Concurrency (콜드 스타트 방지, 비용 추가)
  # → 실제로 필요한지 측정 후 결정
}

# ECS Fargate — Spot 활용 (70% 할인)
resource "aws_ecs_service" "worker" {
  # ...
  capacity_provider_strategy {
    capacity_provider = "FARGATE_SPOT"
    weight            = 80   # 80% Spot
    base              = 0
  }

  capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 20   # 20% On-Demand (안정성)
    base              = 1    # 최소 1개는 On-Demand
  }
}
```

**AWS Budget 설정 (예산 초과 알람)**

```hcl
resource "aws_budgets_budget" "monthly" {
  name         = "monthly-budget"
  budget_type  = "COST"
  limit_amount = "5000"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80   # 80% 도달 시 알람
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = ["aws-billing@mycompany.com"]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"   # 예측 초과 시 선제 알람
    subscriber_email_addresses = ["aws-billing@mycompany.com"]
  }
}

# 서비스별 상세 예산
resource "aws_budgets_budget" "ec2" {
  name         = "ec2-budget"
  budget_type  = "COST"
  limit_amount = "2000"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "Service"
    values = ["Amazon Elastic Compute Cloud - Compute"]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 90
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = ["aws-billing@mycompany.com"]
  }
}
```

---

### 2.3 보안/비용 Best Practice

- **Savings Plans 먼저, RI 나중에**: Compute Savings Plans는 EC2/Lambda/Fargate에 유연하게 적용. 안정적인 사용량이 확정된 서비스는 추가로 RI 검토
- **Spot 인스턴스 활용 기준**: 중단 가능한 워크로드(배치, CI/CD, ML 학습)에만. 상태저장 서비스에는 혼합(일부 On-Demand) 전략 사용
- **NAT GW 비용 주목**: 데이터 처리 요금($0.059/GB)이 EC2 다음으로 큰 비용 항목. Interface VPC Endpoint로 AWS 서비스 트래픽을 우회하면 크게 절감
- **Cost Allocation Tag 필수**: 서비스별/팀별 태그 없이는 비용 원인 파악 불가. Organizations Tag Policy로 미부착 리소스 탐지

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**예상보다 높은 데이터 전송 비용**

```bash
# 리전 간 데이터 전송 비용이 주 원인
# Cost Explorer에서 데이터 전송 비용 상세 조회

aws ce get-cost-and-usage \
  --time-period Start=2024-01-01,End=2024-02-01 \
  --granularity MONTHLY \
  --filter '{"Dimensions":{"Key":"USAGE_TYPE_GROUP","Values":["EC2: Data Transfer - Internet (Out)"]}}' \
  --metrics "UnblendedCost" "UsageQuantity"

# 주요 원인:
# 1. 같은 리전 다른 AZ 간 트래픽 ($0.01/GB)
# 2. EC2 → 인터넷 직접 전송 (NAT GW 대신 Public IP)
# 3. S3 → 인터넷 전송 (S3 Transfer Acceleration 검토)

# AZ 간 트래픽 최소화: 같은 AZ에 EC2와 RDS/Cache 배치
# (고가용성 vs 비용 트레이드오프)
```

**Savings Plans 활용률 저조**

```bash
# 현재 Savings Plans 활용률 확인
aws ce get-savings-plans-utilization \
  --time-period Start=2024-01-01,End=2024-02-01 \
  --query 'Total.{Utilization:Utilization,UnusedSavings:UnusedSavings}'

# 활용률이 낮으면 약정 금액이 과다. Savings Plans 마켓플레이스에서 판매 가능
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: Spot 인스턴스 중단 시 어떻게 대응하나요?**
A: EC2 중단 2분 전에 인스턴스 메타데이터(`/latest/meta-data/spot/interruption-action`)와 EventBridge 이벤트로 알림이 옵니다. Node Termination Handler(EKS), ASG의 Mixed Instances Policy, 또는 자체 신호 핸들러로 처리하세요. (`ec2-spot-instance.md` 참고)

**Q: Reserved Instance를 잘못 구매했는데 환불되나요?**
A: 기본적으로 불가합니다. 단 Convertible RI는 다른 인스턴스 타입으로 교환 가능합니다. RI Marketplace에서 남은 기간을 타인에게 판매할 수 있습니다(Standard RI만).

---

## 4. 모니터링 및 알람

```hcl
# 일별 비용 급등 알람 (전날 대비 20% 이상 증가)
resource "aws_cloudwatch_metric_alarm" "daily_cost_spike" {
  alarm_name          = "daily-cost-spike"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "EstimatedCharges"
  namespace           = "AWS/Billing"
  period              = 86400   # 1일
  statistic           = "Maximum"
  threshold           = 200   # $200/일 초과 시 알람 (조직별 조정)

  # Billing 지표는 us-east-1에서만 확인 가능
  dimensions = {
    Currency = "USD"
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
}
```

**Compute Optimizer 활용**

```bash
# EC2 Right-Sizing 추천 (과다 프로비저닝 탐지)
aws compute-optimizer get-ec2-instance-recommendations \
  --query 'instanceRecommendations[?finding==`OVER_PROVISIONED`].{
    Instance:instanceArn,
    CurrentType:currentInstanceType,
    Recommended:recommendationOptions[0].instanceType,
    EstimatedMonthlySavings:recommendationOptions[0].estimatedMonthlySavings.value
  }' \
  --output table

# EBS 볼륨 최적화 추천
aws compute-optimizer get-ebs-volume-recommendations \
  --query 'volumeRecommendations[?finding==`OVER_PROVISIONED`].{
    Volume:volumeArn,
    CurrentType:currentConfiguration.volumeType,
    CurrentSize:currentConfiguration.volumeSize
  }'
```

---

## 5. TIP

- **FinOps 정기 리뷰**: 월 1회 Cost Explorer + Compute Optimizer + Trusted Advisor를 검토하는 루틴 수립. 자동화보다 정기적인 인간의 검토가 더 효과적
- **Graviton3(ARM64) 전환**: EC2, Lambda, RDS, ElastiCache 모두 Graviton 지원. 같은 성능에 20~40% 저렴. `ec2-instance-types.md` 참고
- **S3 비용 최적화**: Lifecycle 정책 + Intelligent-Tiering 조합으로 스토리지 비용 30~70% 절감 가능. (`s3-lifecycle-intelligent-tiering.md` 참고)
- **AWS Cost Anomaly Detection**: 머신러닝 기반 비용 이상 탐지. Budget보다 정교하게 비용 급등을 사전 감지. 무료 서비스
