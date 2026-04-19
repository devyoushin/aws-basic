# EC2 Spot 인스턴스 운영

## 1. 개요

Spot 인스턴스는 AWS의 유휴 EC2 용량을 활용하는 인스턴스로, On-Demand 대비 최대 90% 저렴하다.
단, AWS가 용량을 회수할 때 2분 전 알림과 함께 인스턴스가 중단(Interruption)될 수 있어 내결함성(Fault-tolerant) 워크로드에 적합하다.

---

## 2. 설명

### 2.1 핵심 개념

**Spot 인스턴스 동작 원리**
- AWS는 특정 인스턴스 타입/AZ 조합별로 Spot 가격을 실시간으로 결정
- Spot 가격은 수요와 공급에 따라 변동 (과거 대비 현재는 매우 안정적)
- 용량 회수 시: 2분 전 **Spot Instance Interruption Notice** 발생
  - EC2 인스턴스 메타데이터 (`/latest/meta-data/spot/termination-time`) 에서 확인
  - EventBridge 이벤트로도 수신 가능

**중단 유형**
| 유형 | 설명 |
|------|------|
| `terminate` | 인스턴스 종료 (기본값) |
| `stop` | 중단 후 정지 (EBS 지원 인스턴스만) |
| `hibernate` | 메모리 상태 저장 후 정지 |

**적합한 워크로드**
- 배치 처리, ML 학습, 빅데이터 분석 (Spark, EMR)
- CI/CD 빌드 워커
- 상태 없는(Stateless) 웹 서버
- Karpenter / ASG mixed instances 정책으로 실행되는 EKS 워커 노드

**부적합한 워크로드**
- 상태 저장(Stateful) DB, 단일 인스턴스 캐시 서버
- 실시간 결제, 세션 유지 필요 서비스
- 중단 처리 로직 없는 레거시 애플리케이션

---

### 2.2 실무 적용 코드

**Auto Scaling Group — Mixed Instances Policy (Spot + On-Demand)**

```hcl
resource "aws_autoscaling_group" "app" {
  name                = "app-asg"
  min_size            = 2
  max_size            = 20
  desired_capacity    = 4
  vpc_zone_identifier = var.private_subnet_ids

  mixed_instances_policy {
    instances_distribution {
      on_demand_base_capacity                  = 2      # 최소 On-Demand 2대 보장
      on_demand_percentage_above_base_capacity = 0      # 나머지는 100% Spot
      spot_allocation_strategy                 = "price-capacity-optimized"
    }

    launch_template {
      launch_template_specification {
        launch_template_id = aws_launch_template.app.id
        version            = "$Latest"
      }

      # 인스턴스 다양화 — 여러 타입으로 중단 위험 분산
      override {
        instance_type = "m5.xlarge"
      }
      override {
        instance_type = "m5a.xlarge"
      }
      override {
        instance_type = "m6i.xlarge"
      }
      override {
        instance_type = "m6a.xlarge"
      }
      override {
        instance_type = "m5.2xlarge"
        weighted_capacity = "2"
      }
    }
  }

  tag {
    key                 = "Name"
    value               = "app-spot-node"
    propagate_at_launch = true
  }
}
```

**AWS Node Termination Handler — Spot 중단 사전 처리 (EKS)**

```bash
# Helm으로 Node Termination Handler 설치
helm repo add eks https://aws.github.io/eks-charts
helm install aws-node-termination-handler \
  eks/aws-node-termination-handler \
  --namespace kube-system \
  --set enableSpotInterruptionDraining=true \
  --set enableScheduledEventDraining=true \
  --set enableRebalanceMonitoring=true
```

**EventBridge — Spot 중단 알림 수신**

```hcl
resource "aws_cloudwatch_event_rule" "spot_interruption" {
  name        = "spot-interruption-warning"
  description = "Spot 인스턴스 중단 2분 전 알림"

  event_pattern = jsonencode({
    source      = ["aws.ec2"]
    detail-type = ["EC2 Spot Instance Interruption Warning"]
  })
}

resource "aws_cloudwatch_event_target" "spot_interruption_sns" {
  rule      = aws_cloudwatch_event_rule.spot_interruption.name
  target_id = "SendToSNS"
  arn       = aws_sns_topic.alerts.arn
}
```

**IMDSv2로 Spot 중단 시간 확인 (인스턴스 내부 스크립트)**

```bash
#!/bin/bash
# Spot 중단 임박 여부 확인 (2분 루프 감지)
TOKEN=$(curl -sS -X PUT "http://169.254.169.254/latest/api/token" \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")

while true; do
  TERMINATION_TIME=$(curl -sS -H "X-aws-ec2-metadata-token: $TOKEN" \
    "http://169.254.169.254/latest/meta-data/spot/termination-time" 2>/dev/null)

  if [ -n "$TERMINATION_TIME" ]; then
    echo "Spot 중단 예정: $TERMINATION_TIME — graceful shutdown 시작"
    # 여기서 실행 중인 작업을 체크포인트 저장하거나 SQS에 반환
    systemctl stop myapp
    break
  fi
  sleep 5
done
```

---

### 2.3 보안/비용 Best Practice

- **인스턴스 다양화**: 최소 4~6개 인스턴스 타입 지정 (단일 타입은 동시 중단 위험)
- **AZ 다양화**: 최소 2개 이상 AZ에 분산
- **`price-capacity-optimized` 전략 사용**: 가격 최저보다 가용성 높은 풀 선택 (AWS 권장)
- **On-Demand Base 설정**: 핵심 서비스는 On-Demand 최소 대수 보장
- **체크포인트 설계**: 배치 작업은 중간 결과를 S3에 주기적으로 저장
- **Savings Plans + Spot 조합**: On-Demand 부분은 Compute Savings Plans 적용

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**InsufficientInstanceCapacity — 특정 인스턴스 타입/AZ 용량 부족**

증상: ASG 스케일 아웃 실패, `InsufficientInstanceCapacity` 오류
원인: 해당 인스턴스 타입이 해당 AZ에서 일시적으로 고갈
해결:
```bash
# 인스턴스 타입 종류 늘리기 (override 추가)
# 또는 다른 AZ 서브넷 추가

# Spot 가용 용량 사전 확인
aws ec2 describe-spot-instance-requests \
  --filters "Name=state,Values=active" \
  --query 'SpotInstanceRequests[*].[InstanceType,AvailabilityZone,State]'
```

**중단 후 작업 손실**

증상: Spot 중단 시 진행 중이던 배치 작업 유실
원인: 중단 처리 로직 없음
해결:
- SQS 기반 작업 큐 사용 — 중단 시 메시지 다시 큐로 반환 (visibility timeout 활용)
- 작업 중간 상태를 S3/DynamoDB에 체크포인트 저장

### 3.2 자주 발생하는 문제 (Q&A)

**Q: Spot 가격이 On-Demand를 초과할 수 있나요?**
A: 현재 AWS Spot 가격 모델에서는 사전 설정한 최고 입찰가를 초과하면 중단됩니다. 최고 입찰가를 On-Demand 가격으로 설정하면 가격 초과로 인한 중단은 없지만, 용량 부족 중단은 여전히 발생합니다.

**Q: Spot 인스턴스가 예상보다 자주 중단됩니다**
A: 단일 인스턴스 타입/AZ 사용 중일 가능성이 높습니다. `price-capacity-optimized` 전략과 함께 4개 이상 인스턴스 타입 다양화를 적용하세요.

**Q: EKS에서 Spot 노드가 중단될 때 Pod가 바로 Evict 되지 않습니다**
A: AWS Node Termination Handler가 설치되어 있는지 확인하세요. 없으면 2분 내에 graceful drain이 이루어지지 않습니다.

---

## 4. 모니터링 및 알람

```hcl
# Spot 중단 이벤트 수 추적
resource "aws_cloudwatch_metric_alarm" "spot_interruptions" {
  alarm_name          = "spot-interruption-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "SpotInterruptions"
  namespace           = "AWS/EC2Spot"
  period              = 300
  statistic           = "Sum"
  threshold           = 3
  alarm_description   = "5분 내 Spot 중단 3회 이상"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
```

**핵심 지표**

| 지표 | 네임스페이스 | 의미 |
|------|------------|------|
| `SpotInterruptions` | AWS/EC2Spot | 중단된 Spot 인스턴스 수 |
| `AvailableInstancePoolsCount` | AWS/EC2Spot | 가용한 인스턴스 풀 수 |
| `GroupSpotInstances` | AWS/AutoScaling | ASG 내 현재 Spot 인스턴스 수 |

---

## 5. TIP

- **Spot Instance Advisor** 활용: 인스턴스 타입별 중단 빈도와 절감률을 사전 확인
- **Rebalance Recommendation 이벤트**: 중단 전 미리 교체 기회를 주는 신호 — Node Termination Handler가 이를 감지해 선제적 drain 가능
- EKS에서 Karpenter 사용 시 Spot 관리가 더 단순해짐 — NodePool에 `capacity-type: spot` 지정만으로 자동 처리
- 배치 작업은 AWS Batch + Spot를 조합하면 중단 재시도 로직을 AWS가 대신 처리해 줌
