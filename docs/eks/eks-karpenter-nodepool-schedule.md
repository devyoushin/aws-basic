# Karpenter NodePool 스케줄 스케일다운 (야간/휴일 비용 절감)

## 1. 개요

dev/stg 환경은 업무 시간 외(야간, 주말)에 EKS worker node가 전혀 필요 없다.
Karpenter의 `NodePool.spec.limits`를 `0`으로 패치하면 신규 노드 프로비저닝이 차단되고,
기존의 빈 노드는 Consolidation으로 자동 제거된다.

EventBridge Scheduler → Lambda(kubectl) 조합으로 이 과정을 완전 자동화한다.

```
EventBridge Scheduler (cron)
  │
  │  야간 21:00 KST       아침 08:00 KST
  ▼                        ▼
Lambda (scale-down)     Lambda (scale-up)
  │                        │
  │ kubectl patch           │ kubectl patch
  ▼                        ▼
NodePool limits          NodePool limits
cpu: "0"                 cpu: "200"
memory: "0Gi"            memory: "400Gi"
  │
  ▼ (Pending 파드 없음 → Karpenter Consolidation)
노드 자동 종료
```

> **작동 원리**: limits=0이면 Karpenter는 어떤 파드가 Pending이 되더라도
> 새 노드를 프로비저닝하지 않는다. 기존 노드에 파드가 없으면 Consolidation이
> `WhenEmpty` 정책에 따라 자동으로 노드를 종료한다.

---

## 2. 설명

### 2.1 NodePool limits 동작 원리

```yaml
# limits=0 설정 시 → 노드 프로비저닝 완전 차단
apiVersion: karpenter.sh/v1beta1
kind: NodePool
metadata:
  name: general-purpose
spec:
  limits:
    cpu: "0"       # 0 설정 시 한도 초과로 신규 프로비저닝 거부
    memory: "0Gi"
  disruption:
    consolidationPolicy: WhenEmpty        # 빈 노드 즉시 제거
    consolidateAfter: 30s                 # 30초 후 빈 노드 종료
```

```yaml
# limits 복원 시 → 정상 운영
spec:
  limits:
    cpu: "200"
    memory: "400Gi"
  disruption:
    consolidationPolicy: WhenUnderutilized
    consolidateAfter: 1m
```

**limits 값 선택 기준**

| 환경 | limits.cpu | limits.memory | 비고 |
|------|-----------|---------------|------|
| 야간/휴일 (차단) | `"0"` | `"0Gi"` | 프로비저닝 완전 차단 |
| 업무시간 (복원) | 클러스터 최대 허용 값 | 클러스터 최대 허용 값 | 실제 사용량의 2~3배 여유 |

> **주의**: limits를 삭제(제거)하면 무제한이 된다. `"0"`으로 명시적으로 설정해야 한다.

---

### 2.2 Lambda 구현 — kubectl로 NodePool 패치

Lambda는 EKS 클러스터 내부에 있지 않으므로 kubectl을 컨테이너 이미지에 포함시켜
IRSA 자격증명으로 EKS API 서버에 직접 접근한다.

**Lambda 컨테이너 이미지 (Dockerfile)**

```dockerfile
FROM public.ecr.aws/lambda/python:3.12

# kubectl 설치
ARG KUBECTL_VERSION=1.30.0
RUN curl -LO "https://dl.k8s.io/release/v${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
    && chmod +x kubectl \
    && mv kubectl /usr/local/bin/

# AWS CLI v2 (kubeconfig 갱신용)
RUN curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o awscliv2.zip \
    && unzip awscliv2.zip \
    && ./aws/install \
    && rm -rf awscliv2.zip aws/

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY handler.py .
CMD ["handler.lambda_handler"]
```

**requirements.txt**

```
boto3>=1.34.0
```

**handler.py — 스케일다운/업 통합 핸들러**

```python
import os
import json
import subprocess
import boto3

CLUSTER_NAME  = os.environ["CLUSTER_NAME"]
REGION        = os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-2")
NODEPOOLS     = os.environ.get("NODEPOOLS", "general-purpose").split(",")

# 야간 차단 시 limits
LIMITS_OFF = {"cpu": "0", "memory": "0Gi"}

# 업무시간 복원 시 limits (환경변수로 주입)
LIMITS_ON = {
    "cpu":    os.environ.get("LIMITS_CPU",    "200"),
    "memory": os.environ.get("LIMITS_MEMORY", "400Gi"),
}


def refresh_kubeconfig():
    """IRSA 자격증명으로 kubeconfig 갱신"""
    subprocess.run(
        [
            "aws", "eks", "update-kubeconfig",
            "--name",   CLUSTER_NAME,
            "--region", REGION,
        ],
        check=True,
        capture_output=True,
    )


def patch_nodepool(name: str, limits: dict):
    patch = json.dumps({"spec": {"limits": limits}})
    result = subprocess.run(
        [
            "kubectl", "patch", "nodepool", name,
            "--type=merge",
            "-p", patch,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"kubectl patch 실패 [{name}]: {result.stderr}"
        )
    print(f"[OK] NodePool {name} limits → {limits}")


def lambda_handler(event, context):
    """
    event 예시:
      {"action": "scale_down"}   # 야간/휴일 적용
      {"action": "scale_up"}     # 업무시간 복원
    """
    action = event.get("action")
    if action not in ("scale_down", "scale_up"):
        raise ValueError(f"Unknown action: {action}")

    limits = LIMITS_OFF if action == "scale_down" else LIMITS_ON

    refresh_kubeconfig()

    errors = []
    for nodepool in NODEPOOLS:
        try:
            patch_nodepool(nodepool.strip(), limits)
        except Exception as e:
            errors.append(str(e))

    if errors:
        raise RuntimeError("\n".join(errors))

    return {
        "statusCode": 200,
        "action":     action,
        "nodepools":  NODEPOOLS,
        "limits":     limits,
    }
```

---

### 2.3 Terraform — 전체 인프라 구성

```hcl
# ────────────────────────────────────────────
# Lambda IAM Role (IRSA 방식)
# ────────────────────────────────────────────
data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "nodepool_scheduler" {
  name               = "eks-nodepool-scheduler"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.nodepool_scheduler.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# EKS kubeconfig 갱신에 필요한 최소 권한
resource "aws_iam_role_policy" "eks_describe" {
  role = aws_iam_role.nodepool_scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "eks:DescribeCluster",     # update-kubeconfig 에 필요
        "eks:ListClusters",
      ]
      Resource = "*"
    }]
  })
}

# ────────────────────────────────────────────
# EKS aws-auth (또는 Access Entry) — Lambda Role 허용
# Kubernetes RBAC: NodePool 패치 권한 부여
# ────────────────────────────────────────────
# aws-auth ConfigMap에 아래 내용 추가 (eksctl 또는 직접 kubectl apply):
#
# mapRoles:
#   - rolearn: arn:aws:iam::123456789012:role/eks-nodepool-scheduler
#     username: eks-nodepool-scheduler
#     groups: []   # RBAC ClusterRole에서 직접 바인딩
#
# ClusterRole + ClusterRoleBinding:
# ---
# apiVersion: rbac.authorization.k8s.io/v1
# kind: ClusterRole
# metadata:
#   name: nodepool-patcher
# rules:
# - apiGroups: ["karpenter.sh"]
#   resources: ["nodepools"]
#   verbs: ["get", "list", "patch", "update"]
# ---
# apiVersion: rbac.authorization.k8s.io/v1
# kind: ClusterRoleBinding
# metadata:
#   name: nodepool-patcher-lambda
# subjects:
# - kind: User
#   name: eks-nodepool-scheduler
#   apiGroup: rbac.authorization.k8s.io
# roleRef:
#   kind: ClusterRole
#   name: nodepool-patcher
#   apiGroup: rbac.authorization.k8s.io

# ────────────────────────────────────────────
# Lambda 함수 (컨테이너 이미지)
# ────────────────────────────────────────────
resource "aws_ecr_repository" "nodepool_scheduler" {
  name = "eks-nodepool-scheduler"
}

resource "aws_lambda_function" "nodepool_scheduler" {
  function_name = "eks-nodepool-scheduler"
  role          = aws_iam_role.nodepool_scheduler.arn

  package_type = "Image"
  image_uri    = "${aws_ecr_repository.nodepool_scheduler.repository_url}:latest"

  timeout      = 60
  memory_size  = 256

  environment {
    variables = {
      CLUSTER_NAME   = var.eks_cluster_name
      NODEPOOLS      = "general-purpose,spot-nodepool"  # 콤마 구분
      LIMITS_CPU     = "200"
      LIMITS_MEMORY  = "400Gi"
    }
  }
}

# ────────────────────────────────────────────
# EventBridge Scheduler — 야간 스케일다운
# ────────────────────────────────────────────
resource "aws_scheduler_schedule" "nodepool_scale_down" {
  name       = "eks-nodepool-scale-down"
  group_name = "default"

  flexible_time_window {
    mode = "OFF"
  }

  # 평일 21:00 KST (UTC 12:00)
  schedule_expression          = "cron(0 12 ? * MON-FRI *)"
  schedule_expression_timezone = "Asia/Seoul"

  target {
    arn      = aws_lambda_function.nodepool_scheduler.arn
    role_arn = aws_iam_role.eventbridge_scheduler.arn

    input = jsonencode({ action = "scale_down" })
  }
}

resource "aws_scheduler_schedule" "nodepool_scale_up" {
  name       = "eks-nodepool-scale-up"
  group_name = "default"

  flexible_time_window {
    mode = "OFF"
  }

  # 평일 08:00 KST
  schedule_expression          = "cron(0 8 ? * MON-FRI *)"
  schedule_expression_timezone = "Asia/Seoul"

  target {
    arn      = aws_lambda_function.nodepool_scheduler.arn
    role_arn = aws_iam_role.eventbridge_scheduler.arn

    input = jsonencode({ action = "scale_up" })
  }
}

# 주말 전체 차단 (금요일 21:00에 scale_down → 월요일 08:00에 scale_up으로 커버됨)
# 공휴일은 아래 수동 트리거 또는 별도 자동화 참고

# ────────────────────────────────────────────
# EventBridge → Lambda 호출 권한
# ────────────────────────────────────────────
resource "aws_iam_role" "eventbridge_scheduler" {
  name = "eventbridge-nodepool-scheduler"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "eventbridge_invoke_lambda" {
  role = aws_iam_role.eventbridge_scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.nodepool_scheduler.arn
    }]
  })
}

resource "aws_lambda_permission" "allow_scheduler" {
  statement_id  = "AllowEventBridgeScheduler"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.nodepool_scheduler.function_name
  principal     = "scheduler.amazonaws.com"
  source_arn    = aws_scheduler_schedule.nodepool_scale_down.arn
}
```

---

### 2.4 공휴일 수동 트리거 / 임시 차단

EventBridge cron은 날짜 기반 공휴일을 자동 처리할 수 없다.
공휴일에는 아래 방법으로 수동 또는 별도 자동화를 적용한다.

**방법 A — CLI로 Lambda 직접 호출 (임시)**

```bash
# 공휴일 아침: 수동으로 scale_down 적용
aws lambda invoke \
  --function-name eks-nodepool-scheduler \
  --payload '{"action":"scale_down"}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/response.json && cat /tmp/response.json

# 공휴일 다음날 업무시작 전: scale_up 복원
aws lambda invoke \
  --function-name eks-nodepool-scheduler \
  --payload '{"action":"scale_up"}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/response.json
```

**방법 B — 연간 공휴일 EventBridge 일정 추가 (Terraform)**

```hcl
# 1월 1일 00:00 KST scale_down (신정)
resource "aws_scheduler_schedule" "holiday_new_year_down" {
  name = "holiday-new-year-scale-down"

  flexible_time_window { mode = "OFF" }

  schedule_expression          = "cron(0 0 1 1 ? 2025)"
  schedule_expression_timezone = "Asia/Seoul"

  target {
    arn      = aws_lambda_function.nodepool_scheduler.arn
    role_arn = aws_iam_role.eventbridge_scheduler.arn
    input    = jsonencode({ action = "scale_down" })
  }
}
```

**방법 C — 공휴일 API 활용 자동화 (Python 스크립트)**

```python
# 매년 초 공공데이터 포털 API로 공휴일 목록 조회 후
# EventBridge 일정 자동 등록 (별도 관리 Lambda)

import boto3
import requests
from datetime import datetime

HOLIDAY_API_URL = "https://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/getRestDeInfo"

def register_holiday_schedules(year: int):
    # 공공데이터 포털 API로 공휴일 조회
    response = requests.get(HOLIDAY_API_URL, params={
        "serviceKey": "<API_KEY>",
        "solYear": year,
        "numOfRows": 100,
        "_type": "json",
    })
    holidays = response.json()["response"]["body"]["items"]["item"]

    scheduler = boto3.client("scheduler", region_name="ap-northeast-2")

    for holiday in holidays:
        date_str = str(holiday["locdate"])   # 예: "20250101"
        dt = datetime.strptime(date_str, "%Y%m%d")

        # 해당 날 00:00 KST scale_down
        scheduler.create_schedule(
            Name=f"holiday-{date_str}-down",
            ScheduleExpression=f"cron(0 0 {dt.day} {dt.month} ? {dt.year})",
            ScheduleExpressionTimezone="Asia/Seoul",
            FlexibleTimeWindow={"Mode": "OFF"},
            Target={
                "Arn": "<LAMBDA_ARN>",
                "RoleArn": "<SCHEDULER_ROLE_ARN>",
                "Input": '{"action":"scale_down"}',
            },
        )
```

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**scale_down 후 노드가 종료되지 않음**

limits=0으로 패치해도 기존 노드의 파드가 남아 있으면 Consolidation이 노드를 제거하지 않는다.

```bash
# 1. NodePool limits 패치 확인
kubectl get nodepool general-purpose -o jsonpath='{.spec.limits}'

# 2. 각 노드의 파드 현황 확인 (DaemonSet 파드만 있어야 빈 노드로 인식)
kubectl get pods --all-namespaces -o wide | grep <node-name>

# 3. DaemonSet 파드만 남은 경우 — Karpenter는 이를 "empty"로 취급
# consolidateAfter 시간(기본 30s) 후 자동 종료됨

# 4. 강제 종료가 필요한 경우 (Deployment 복제 수 확인)
kubectl get deployments --all-namespaces \
  | awk 'NR>1 && $3 > 0 {print $1, $2}'
```

**업무시간 외에 노드가 갑자기 떠 있음**

limits=0이어도 DaemonSet은 파드 생성을 시도한다. DaemonSet 파드는 노드에 바인딩되어 있으므로
새 노드를 유발하지는 않는다. 그러나 아래 경우는 신규 노드 프로비저닝 시도가 발생할 수 있다.

```bash
# PodDisruptionBudget이 Consolidation을 막고 있는지 확인
kubectl get pdb --all-namespaces

# NodePool limits 초과 여부 확인 (이벤트 로그)
kubectl describe nodepool general-purpose | grep -A5 Events

# Karpenter 로그에서 limits 차단 메시지 확인
kubectl logs -n karpenter -l app.kubernetes.io/name=karpenter \
  | grep -i "exceeded limits\|limit"
```

**Lambda 실행 오류 — kubectl 권한 없음**

```bash
# Lambda Role이 EKS aws-auth(또는 Access Entry)에 등록됐는지 확인
kubectl get configmap aws-auth -n kube-system -o yaml | grep nodepool-scheduler

# Access Entry 방식 (EKS 1.30+)
aws eks list-access-entries --cluster-name <cluster-name> \
  | grep nodepool-scheduler

# RBAC 권한 확인
kubectl auth can-i patch nodepools \
  --as eks-nodepool-scheduler \
  --as-group system:masters  # 또는 지정 그룹
```

**Lambda 타임아웃**

```bash
# kubeconfig 갱신 + kubectl patch가 60초 내에 완료되지 않는 경우
# → Lambda timeout을 120초로 늘리거나, VPC 내 Lambda로 배포 시 ENI 생성 지연 고려

# Lambda를 VPC 내 배포 시 EKS API 서버 Private Endpoint 활성화 필요
aws eks describe-cluster \
  --name <cluster-name> \
  --query 'cluster.resourcesVpcConfig.{Public:endpointPublicAccess,Private:endpointPrivateAccess}'
```

### 3.2 주의사항 체크리스트

| 항목 | 내용 |
|------|------|
| StatefulSet 데이터 보호 | PVC는 노드 종료 후에도 유지됨. 단, 동일 AZ에 노드가 재생성되어야 마운트 가능. StorageClass `volumeBindingMode: WaitForFirstConsumer` 권장 |
| CronJob 스케줄 충돌 | 야간에 실행되는 K8s CronJob이 있으면 Pending 상태로 멈춤. limits=0 시간대 이전에 완료되도록 스케줄 조정 |
| HPA 최소 복제 수 | `minReplicas > 0`인 HPA가 있으면 노드가 없어도 Pending 파드가 계속 생성됨. 환경변수 또는 별도 스크립트로 `minReplicas=0`으로 같이 패치 |
| Karpenter Disruption Budget | `NodePool.spec.disruption.budgets` 설정이 있으면 Consolidation 속도가 느려짐 |
| 복구 시간 | 아침 scale_up 후 노드가 Ready까지 1~3분 소요. 첫 파드 배포 완료는 scale_up 후 약 3~5분 예상 |

---

## 4. 모니터링 및 알람

```hcl
# scale_down Lambda 실패 알람 (야간에 노드가 계속 켜져 있을 위험)
resource "aws_cloudwatch_metric_alarm" "nodepool_scheduler_error" {
  alarm_name          = "eks-nodepool-scheduler-error"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 0

  dimensions = {
    FunctionName = aws_lambda_function.nodepool_scheduler.function_name
  }

  alarm_description = "NodePool 스케줄러 Lambda 오류 — 수동 확인 필요"
  alarm_actions     = [aws_sns_topic.alerts.arn]
}

# 야간 시간대에 EC2(EKS 노드)가 여전히 Running인지 확인
# (Lambda가 실패했을 때 비용이 계속 발생하는 상황 감지)
resource "aws_cloudwatch_metric_alarm" "eks_nodes_after_hours" {
  alarm_name          = "eks-dev-nodes-after-hours"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3   # 15분 유지 시 알람
  metric_name         = "cluster_node_count"
  namespace           = "ContainerInsights"
  period              = 300
  statistic           = "Maximum"
  threshold           = 0

  dimensions = {
    ClusterName = var.eks_cluster_name
    NodeType    = "worker"
  }

  # 이 알람은 야간 시간대에만 활성화 (CloudWatch Composite Alarm 활용 가능)
  alarm_description = "야간 시간대에 EKS 노드가 Running — NodePool 스케줄러 실패 의심"
  alarm_actions     = [aws_sns_topic.alerts.arn]
}
```

**Lambda 실행 로그 확인**

```bash
# 가장 최근 scale_down 실행 로그
aws logs filter-log-events \
  --log-group-name /aws/lambda/eks-nodepool-scheduler \
  --filter-pattern '"scale_down"' \
  --start-time $(date -d '24 hours ago' +%s000) \
  --query 'events[*].{Time:timestamp,Message:message}' \
  --output table

# 오류 로그만 추출
aws logs filter-log-events \
  --log-group-name /aws/lambda/eks-nodepool-scheduler \
  --filter-pattern '"ERROR" OR "kubectl patch 실패"' \
  --start-time $(date -d '7 days ago' +%s000)
```

---

## 5. TIP

- **`WhenEmpty` vs `WhenUnderutilized`**: 야간 차단 효과를 빠르게 보려면 scale_down 전에 NodePool disruption policy를 `WhenEmpty`로 잠시 바꾸는 것도 방법이다. 아침 scale_up 시에는 `WhenUnderutilized`로 복원.
- **HPA minReplicas 동시 패치**: 야간에 Pending 파드가 없어야 완전히 꺼진다. scale_down Lambda에서 NodePool 패치와 함께 `kubectl scale deploy --all --replicas=0 -n <dev-ns>` 도 같이 실행하면 더 확실하다.
- **Namespace 단위 격리**: dev/stg 전용 NodePool을 별도로 만들고, `spec.template.metadata.labels`로 네임스페이스 격리를 해두면 prod NodePool에 영향 없이 안전하게 패치 가능.
- **Karpenter v1 마이그레이션**: Karpenter v0.33 이상에서는 `v1beta1` → `v1` API로 변경됨. `kubectl get nodepool -o yaml`로 `apiVersion` 확인 후 패치 명령어 조정 필요.
- **비용 절감 효과 추정**: dev/stg 환경 기준 야간(21:00~08:00, 11시간) + 주말(48시간) = 주당 약 103시간 절약 → 주 168시간 중 61% 절감. On-Demand m5.xlarge($0.192/h) × 5대 기준 월 약 $540 절감.
