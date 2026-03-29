# CloudWatch Container Insights

## 1. 개요

Container Insights는 EKS, ECS, EC2 기반 컨테이너 워크로드의 CPU, 메모리, 네트워크, 디스크 지표를 수집해 CloudWatch에서 시각화하는 기능이다.
노드·Pod·컨테이너·서비스 단위의 계층적 메트릭을 제공하며, 이상 탐지와 알람을 연계해 인프라 문제를 빠르게 발견할 수 있다.
EKS에서는 CloudWatch Observability 애드온으로 설치하고, Fluent Bit으로 로그도 함께 수집한다.

---

## 2. 설명

### 2.1 핵심 개념

**Container Insights 지표 계층**

```
Cluster 레벨
  └── Node 레벨 (EC2 인스턴스)
        └── Pod 레벨 (Kubernetes Pod)
              └── Container 레벨 (개별 컨테이너)
                    └── Service 레벨 (Kubernetes Service)
```

**수집되는 주요 지표 (네임스페이스: ContainerInsights)**

| 지표 | 설명 | 단위 |
|------|------|------|
| `node_cpu_utilization` | 노드 CPU 사용률 | % |
| `node_memory_utilization` | 노드 메모리 사용률 | % |
| `node_filesystem_utilization` | 노드 디스크 사용률 | % |
| `pod_cpu_utilization` | Pod CPU 사용률 (Request 대비) | % |
| `pod_memory_utilization` | Pod 메모리 사용률 (Request 대비) | % |
| `pod_cpu_utilization_over_pod_limit` | Pod CPU Limit 초과율 | % |
| `pod_memory_utilization_over_pod_limit` | Pod Memory Limit 초과율 | % |
| `pod_network_rx_bytes` | Pod 수신 바이트 | bytes/sec |
| `pod_network_tx_bytes` | Pod 송신 바이트 | bytes/sec |
| `cluster_node_count` | 클러스터 노드 수 | count |
| `cluster_failed_node_count` | 실패 노드 수 | count |

**Enhanced Observability (추가 지표)**

```
기본 Container Insights:
  - 노드/Pod 레벨 CPU, 메모리, 네트워크
  - $0.0135/1,000 지표/월

Enhanced Observability (추가 비용):
  - 컨테이너 레벨 세분화 지표
  - kubelet, kube-proxy 상태
  - HTTP 요청 레이턴시 (eBPF 기반)
  - $0.0135/1,000 지표/월 (추가)
```

---

### 2.2 실무 적용 코드

**EKS — CloudWatch Observability 애드온 설치 (Terraform)**

```hcl
# CloudWatch Agent + Fluent Bit을 한번에 설치하는 애드온
resource "aws_eks_addon" "cloudwatch_observability" {
  cluster_name             = aws_eks_cluster.main.name
  addon_name               = "amazon-cloudwatch-observability"
  addon_version            = "v1.7.0-eksbuild.1"
  service_account_role_arn = aws_iam_role.cloudwatch_agent.arn

  configuration_values = jsonencode({
    agent = {
      config = {
        logs = {
          metrics_collected = {
            kubernetes = {
              enhanced_container_insights = true   # Enhanced 활성화
            }
          }
        }
      }
    }
  })
}

# CloudWatch Agent IRSA Role
resource "aws_iam_role" "cloudwatch_agent" {
  name = "eks-cloudwatch-agent"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Federated = aws_iam_openid_connect_provider.eks.arn
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "${replace(aws_iam_openid_connect_provider.eks.url, "https://", "")}:sub" = "system:serviceaccount:amazon-cloudwatch:cloudwatch-agent"
          "${replace(aws_iam_openid_connect_provider.eks.url, "https://", "")}:aud" = "sts.amazonaws.com"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "cloudwatch_agent" {
  role       = aws_iam_role.cloudwatch_agent.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
}
```

**Helm으로 직접 설치 (애드온 미사용 환경)**

```bash
# CloudWatch Observability Helm Chart
helm repo add aws-observability https://aws.github.io/eks-charts

helm install amazon-cloudwatch-observability \
  aws-observability/amazon-cloudwatch-observability \
  --namespace amazon-cloudwatch \
  --create-namespace \
  --set clusterName=my-cluster \
  --set serviceAccount.annotations."eks\.amazonaws\.com/role-arn"=arn:aws:iam::123456789012:role/eks-cloudwatch-agent
```

**CloudWatch Alarms — Pod/Node 핵심 알람**

```hcl
locals {
  cluster_name = "my-eks-cluster"
}

# 노드 메모리 사용률 알람
resource "aws_cloudwatch_metric_alarm" "node_memory_high" {
  alarm_name          = "eks-node-memory-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "node_memory_utilization"
  namespace           = "ContainerInsights"
  period              = 300
  statistic           = "Average"
  threshold           = 85

  dimensions = {
    ClusterName = local.cluster_name
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
}

# Pod가 Memory Limit 초과하는 경우 (OOMKill 위험)
resource "aws_cloudwatch_metric_alarm" "pod_memory_over_limit" {
  alarm_name          = "eks-pod-memory-over-limit"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "pod_memory_utilization_over_pod_limit"
  namespace           = "ContainerInsights"
  period              = 60
  statistic           = "Maximum"
  threshold           = 90   # Limit의 90% 초과

  dimensions = {
    ClusterName = local.cluster_name
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
}

# 클러스터 실패 노드 감지
resource "aws_cloudwatch_metric_alarm" "failed_nodes" {
  alarm_name          = "eks-failed-nodes"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "cluster_failed_node_count"
  namespace           = "ContainerInsights"
  period              = 60
  statistic           = "Maximum"
  threshold           = 0

  dimensions = {
    ClusterName = local.cluster_name
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
}
```

**CloudWatch Logs Insights — Container Insights 로그 분석**

```bash
# 특정 네임스페이스의 OOMKilled 이벤트
fields @timestamp, kubernetes.pod_name, kubernetes.namespace_name, reason
| filter reason = "OOMKilling"
| sort @timestamp desc
| limit 50

# CPU throttling이 심한 컨테이너 탐지
fields @timestamp, kubernetes.container_name, kubernetes.pod_name, cpu_throttled_percent
| filter cpu_throttled_percent > 50
| stats avg(cpu_throttled_percent) as avg_throttle by kubernetes.container_name
| sort avg_throttle desc

# 재시작 횟수가 많은 Pod
fields kubernetes.pod_name, kubernetes.namespace_name, kubernetes.container_restart_count
| stats max(kubernetes.container_restart_count) as restarts by kubernetes.pod_name, kubernetes.namespace_name
| filter restarts > 5
| sort restarts desc
```

---

### 2.3 보안/비용 Best Practice

- **Enhanced Observability는 선택적으로**: 기본 Container Insights도 노드/Pod 레벨 충분. 디버깅 필요 시에만 Enhanced 활성화
- **보존 기간 단축**: Container Insights 로그 그룹(`/aws/containerinsights/*`)의 기본 보존은 Never. 7~14일로 설정해 스토리지 비용 절감
- **네임스페이스 필터링**: 모니터링이 필요 없는 시스템 네임스페이스(`kube-system` 제외 가능)를 제외하면 지표 수와 비용 감소
- **Prometheus + AMG 고려**: 대규모 클러스터는 Container Insights보다 Amazon Managed Prometheus + Grafana 조합이 비용 효율적일 수 있음

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**Container Insights 지표가 CloudWatch에 보이지 않음**

```bash
# CloudWatch Agent DaemonSet 상태 확인
kubectl get pods -n amazon-cloudwatch
kubectl logs -n amazon-cloudwatch -l app=cloudwatch-agent --tail=50

# IRSA 권한 확인
kubectl describe sa cloudwatch-agent -n amazon-cloudwatch
# → eks.amazonaws.com/role-arn annotation 있어야 함

# Agent ConfigMap 확인
kubectl get configmap -n amazon-cloudwatch amazon-cloudwatch-observability-agent-common-config -o yaml

# 실제 메트릭 수집 여부 (Agent 내부)
kubectl exec -n amazon-cloudwatch \
  $(kubectl get pod -n amazon-cloudwatch -l app=cloudwatch-agent -o name | head -1) \
  -- /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl -a status
```

**특정 노드에서만 지표 누락**

```bash
# 해당 노드의 Agent 로그 확인
kubectl logs -n amazon-cloudwatch \
  -l app=cloudwatch-agent \
  --field-selector spec.nodeName=ip-10-0-1-100.ap-northeast-2.compute.internal

# 노드 IAM Role에 CloudWatchAgentServerPolicy 있는지 확인
aws iam list-attached-role-policies \
  --role-name eks-node-role \
  --query 'AttachedPolicies[*].PolicyName'
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: Container Insights와 kube-state-metrics의 차이는?**
A: Container Insights는 CloudWatch에 통합되어 알람/대시보드 연계가 쉽고, AWS 관리형으로 설치가 간단합니다. kube-state-metrics는 Kubernetes 오브젝트 상태(Deployment replica 수, Pod phase 등)를 Prometheus 형식으로 노출해 더 세밀한 메트릭을 제공합니다. 두 가지를 함께 사용하는 경우가 많습니다.

**Q: Fargate 노드에서도 Container Insights가 동작하나요?**
A: Fargate에서는 DaemonSet 실행이 불가해 기본 Container Insights를 쓸 수 없습니다. Fargate용 Fluent Bit sidecar를 Pod에 주입하는 방식으로 로그를 수집하고, 메트릭은 별도 설정이 필요합니다.

---

## 4. 모니터링 및 알람

```hcl
# Container Insights 자동 대시보드 (AWS 제공 기본 대시보드 외 커스텀)
resource "aws_cloudwatch_dashboard" "eks" {
  dashboard_name = "EKS-${local.cluster_name}"

  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric"
        properties = {
          metrics = [
            ["ContainerInsights", "cluster_node_count", "ClusterName", local.cluster_name],
            ["ContainerInsights", "cluster_failed_node_count", "ClusterName", local.cluster_name]
          ]
          period = 60
          title  = "Node Count"
        }
      },
      {
        type = "metric"
        properties = {
          metrics = [
            ["ContainerInsights", "node_cpu_utilization", "ClusterName", local.cluster_name],
            ["ContainerInsights", "node_memory_utilization", "ClusterName", local.cluster_name]
          ]
          period = 60
          title  = "Node Resource Utilization"
        }
      }
    ]
  })
}
```

---

## 5. TIP

- **CloudWatch Container Insights 자동 대시보드**: AWS 콘솔 → CloudWatch → Container Insights에서 클러스터/노드/Pod 레벨 대시보드 자동 생성. 별도 설정 없이 바로 사용 가능
- **Anomaly Detection 연계**: `pod_memory_utilization`에 Anomaly Detection Band를 설정하면 절대값 임계치 없이도 이상 패턴 탐지 가능
- **HPA 연동**: Container Insights 지표를 Custom Metrics로 HPA에 연결 가능. `pod_cpu_utilization_over_pod_limit`을 기준으로 스케일링하면 Limit 대비 사용률로 오토스케일링 가능
- **Prometheus 호환**: CloudWatch Agent는 Prometheus 스크래핑도 지원. 애플리케이션의 `/metrics` 엔드포인트를 CloudWatch에 수집 가능
