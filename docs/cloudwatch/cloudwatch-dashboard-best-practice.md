# CloudWatch 대시보드 설계 Best Practice

## 1. 개요
- CloudWatch 대시보드는 지표, 로그, 알람 상태를 한 화면에서 시각화하는 운영 관제판
- 잘못 설계된 대시보드는 중요한 신호를 놓치거나 노이즈가 많아 On-call 피로도 증가
- USE 메서드(Utilization, Saturation, Errors)와 RED 메서드(Rate, Errors, Duration)를 기반으로 설계하면 장애 원인을 체계적으로 추적 가능

## 2. 설명
### 2.1 핵심 개념

**대시보드 설계 방법론**

| 메서드 | 적용 대상 | 지표 예시 |
|--------|-----------|-----------|
| **USE** | 인프라 리소스 (EC2, RDS, EBS) | CPU/메모리/디스크 사용률, IOPS 포화도, 에러율 |
| **RED** | 서비스/API (ALB, Lambda, API GW) | 초당 요청(Rate), 5XX 에러율(Error), p99 지연(Duration) |
| **Four Golden Signals** | SRE 전체 | 지연(Latency), 트래픽(Traffic), 에러(Errors), 포화도(Saturation) |

**위젯 유형**
| 위젯 | 용도 |
|------|------|
| Line | 시계열 추이, 이상 패턴 탐지 |
| Stacked Area | 트래픽 분포 (정상/에러 비율) |
| Number | 현재값 단일 표시 (요청 수, 에러율) |
| Bar | 구간별 비교 |
| Alarm Status | 알람 현황 한눈에 확인 |
| Log table | Logs Insights 실시간 쿼리 |
| Text | 섹션 제목, Runbook 링크 |

**계층형 대시보드 구조**
```
Level 1 — Executive/Service Overview
  └─ SLA 지표, 가용성 %, 주요 에러율

Level 2 — Service Dashboard (팀별)
  └─ RED 지표, 의존성 서비스 상태

Level 3 — Resource Dashboard (인프라)
  └─ USE 지표 (EC2, RDS, EBS)

Level 4 — Debugging Dashboard (장애 시)
  └─ 상세 로그, 트레이스, 이상 지표
```

### 2.2 실무 적용 코드

**Terraform — 서비스 대시보드 (RED 메서드)**
```hcl
resource "aws_cloudwatch_dashboard" "service_overview" {
  dashboard_name = "prod-service-overview"

  dashboard_body = jsonencode({
    widgets = [
      # ── 섹션 제목 ──
      {
        type   = "text"
        x      = 0; y = 0; width = 24; height = 1
        properties = {
          markdown = "## 🚦 서비스 상태 — Production | [Runbook](https://wiki.example.com/runbook) | [On-call](https://pagerduty.example.com)"
        }
      },

      # ── 알람 상태 ──
      {
        type   = "alarm"
        x      = 0; y = 1; width = 24; height = 2
        properties = {
          title  = "알람 현황"
          alarms = [
            "arn:aws:cloudwatch:ap-northeast-2:123456789012:alarm:prod-api-critical",
            "arn:aws:cloudwatch:ap-northeast-2:123456789012:alarm:prod-db-critical",
            "arn:aws:cloudwatch:ap-northeast-2:123456789012:alarm:prod-cache-critical"
          ]
        }
      },

      # ── Rate: 초당 요청 수 ──
      {
        type   = "metric"
        x      = 0; y = 3; width = 8; height = 6
        properties = {
          title   = "Request Rate (req/s)"
          region  = "ap-northeast-2"
          view    = "timeSeries"
          stacked = false
          metrics = [
            ["AWS/ApplicationELB", "RequestCount",
              "LoadBalancer", "app/prod-alb/abc123",
              { "stat": "Sum", "period": 60, "id": "m1", "visible": false }],
            [{ "expression": "m1/60", "label": "Requests/s", "id": "e1" }]
          ]
          period = 60
        }
      },

      # ── Errors: 에러율 ──
      {
        type   = "metric"
        x      = 8; y = 3; width = 8; height = 6
        properties = {
          title   = "Error Rate (%)"
          region  = "ap-northeast-2"
          view    = "timeSeries"
          metrics = [
            ["AWS/ApplicationELB", "HTTPCode_Target_5XX_Count",
              "LoadBalancer", "app/prod-alb/abc123",
              { "stat": "Sum", "period": 60, "id": "m1", "visible": false }],
            ["AWS/ApplicationELB", "RequestCount",
              "LoadBalancer", "app/prod-alb/abc123",
              { "stat": "Sum", "period": 60, "id": "m2", "visible": false }],
            [{ "expression": "IF(m2>0, (m1/m2)*100, 0)", "label": "5XX Rate %", "id": "e1", "color": "#d62728" }]
          ]
          annotations = {
            horizontal = [{
              label = "SLA 임계값"
              value = 1
              color = "#ff7f0e"
            }]
          }
        }
      },

      # ── Duration: p50/p90/p99 레이턴시 ──
      {
        type   = "metric"
        x      = 16; y = 3; width = 8; height = 6
        properties = {
          title   = "Response Time (ms)"
          region  = "ap-northeast-2"
          view    = "timeSeries"
          metrics = [
            ["AWS/ApplicationELB", "TargetResponseTime",
              "LoadBalancer", "app/prod-alb/abc123",
              { "stat": "p50", "period": 60, "label": "p50" }],
            ["AWS/ApplicationELB", "TargetResponseTime",
              "LoadBalancer", "app/prod-alb/abc123",
              { "stat": "p90", "period": 60, "label": "p90" }],
            ["AWS/ApplicationELB", "TargetResponseTime",
              "LoadBalancer", "app/prod-alb/abc123",
              { "stat": "p99", "period": 60, "label": "p99", "color": "#d62728" }]
          ]
          yAxis = { left = { min = 0 } }
        }
      },

      # ── 인프라: EC2 CPU & 메모리 ──
      {
        type   = "text"
        x      = 0; y = 9; width = 24; height = 1
        properties = { markdown = "### 인프라 상태 (USE 메서드)" }
      },
      {
        type   = "metric"
        x      = 0; y = 10; width = 8; height = 6
        properties = {
          title   = "EC2 CPU Utilization (%)"
          region  = "ap-northeast-2"
          metrics = [
            ["AWS/EC2", "CPUUtilization",
              "AutoScalingGroupName", "prod-api-asg",
              { "stat": "Average", "label": "Average" }],
            ["AWS/EC2", "CPUUtilization",
              "AutoScalingGroupName", "prod-api-asg",
              { "stat": "Maximum", "label": "Maximum", "color": "#d62728" }]
          ]
          annotations = {
            horizontal = [{ value = 80, label = "알람 임계값", color = "#ff7f0e" }]
          }
        }
      },
      {
        type   = "metric"
        x      = 8; y = 10; width = 8; height = 6
        properties = {
          title   = "Memory Utilization (%)"
          region  = "ap-northeast-2"
          metrics = [
            ["Custom/EC2", "mem_used_percent",
              "AutoScalingGroupName", "prod-api-asg",
              { "stat": "Average" }]
          ]
        }
      },
      {
        type   = "metric"
        x      = 16; y = 10; width = 8; height = 6
        properties = {
          title   = "Active Instances"
          region  = "ap-northeast-2"
          metrics = [
            ["AWS/AutoScaling", "GroupInServiceInstances",
              "AutoScalingGroupName", "prod-api-asg",
              { "stat": "Average" }]
          ]
        }
      },

      # ── RDS ──
      {
        type   = "metric"
        x      = 0; y = 16; width = 8; height = 6
        properties = {
          title   = "RDS CPU (%)"
          region  = "ap-northeast-2"
          metrics = [
            ["AWS/RDS", "CPUUtilization",
              "DBClusterIdentifier", "prod-aurora",
              { "stat": "Average" }]
          ]
        }
      },
      {
        type   = "metric"
        x      = 8; y = 16; width = 8; height = 6
        properties = {
          title   = "RDS Database Connections"
          region  = "ap-northeast-2"
          metrics = [
            ["AWS/RDS", "DatabaseConnections",
              "DBClusterIdentifier", "prod-aurora",
              { "stat": "Average", "label": "Writer" }]
          ]
        }
      },
      {
        type   = "metric"
        x      = 16; y = 16; width = 8; height = 6
        properties = {
          title   = "RDS Latency (ms)"
          region  = "ap-northeast-2"
          metrics = [
            ["AWS/RDS", "WriteLatency",
              "DBClusterIdentifier", "prod-aurora",
              { "stat": "Average", "label": "Write", "period": 60 }],
            ["AWS/RDS", "ReadLatency",
              "DBClusterIdentifier", "prod-aurora",
              { "stat": "Average", "label": "Read", "period": 60 }]
          ]
        }
      },

      # ── 로그 인사이트 ──
      {
        type   = "text"
        x      = 0; y = 22; width = 24; height = 1
        properties = { markdown = "### 실시간 로그 분석" }
      },
      {
        type   = "log"
        x      = 0; y = 23; width = 24; height = 6
        properties = {
          title   = "에러 발생 추이 (5분 단위)"
          region  = "ap-northeast-2"
          query   = "SOURCE '/aws/eks/prod-cluster/application' | fields @timestamp, @message | filter @message like /ERROR/ | stats count(*) as errorCount by bin(5m)"
          view    = "timeSeries"
        }
      }
    ]
  })
}
```

**AWS CLI — 대시보드 복사 및 관리**
```bash
# 기존 대시보드 JSON 내보내기
aws cloudwatch get-dashboard \
  --dashboard-name prod-service-overview \
  --query 'DashboardBody' \
  --output text > dashboard-backup.json

# 대시보드 복제 (스테이징용)
DASHBOARD_BODY=$(aws cloudwatch get-dashboard \
  --dashboard-name prod-service-overview \
  --query 'DashboardBody' \
  --output text)

# LoadBalancer ARN 교체 후 스테이징 대시보드 생성
echo "$DASHBOARD_BODY" | \
  sed 's/prod-alb\/abc123/staging-alb\/def456/g' | \
  xargs -I{} aws cloudwatch put-dashboard \
    --dashboard-name staging-service-overview \
    --dashboard-body '{}'

# 모든 대시보드 목록
aws cloudwatch list-dashboards \
  --query 'DashboardEntries[*].{Name:DashboardName,Size:Size}'
```

**Period Override 설정 — 시간 범위에 따른 자동 조정**
```json
{
  "periodOverride": "auto",
  "widgets": [...]
}
```
- `auto`: 콘솔의 시간 범위에 따라 Period 자동 조정 (1시간 → 1분, 1주 → 1시간)
- `inherit`: 각 위젯의 Period 그대로 유지

**Variable — 동적 대시보드 (환경/서비스 선택)**
```hcl
resource "aws_cloudwatch_dashboard" "dynamic" {
  dashboard_name = "dynamic-service-dashboard"

  dashboard_body = jsonencode({
    variables = [
      {
        type  = "property"
        property = "AutoScalingGroupName"
        inputType = "select"
        id    = "asgName"
        label = "Auto Scaling Group"
        visible = true
        defaultValue = "prod-api-asg"
        values = [
          { value = "prod-api-asg",     label = "Prod API" },
          { value = "staging-api-asg",  label = "Staging API" },
          { value = "prod-worker-asg",  label = "Prod Worker" }
        ]
      }
    ]
    widgets = [
      {
        type = "metric"
        properties = {
          title = "CPU Utilization"
          metrics = [
            ["AWS/EC2", "CPUUtilization",
              "AutoScalingGroupName", "${asgName}",
              { "stat": "Average" }]
          ]
        }
      }
    ]
  })
}
```

### 2.3 보안/비용 Best Practice
- 대시보드는 무료 (최초 3개) — 4번째부터 대시보드당 $3/월
- **공유 대시보드**: `aws cloudwatch set-alarm-state` 없이 읽기 전용 공유 URL 제공 가능 (Settings > Share dashboard)
- 외부 이해관계자용 공유 시 민감 지표(비용, 사용자 수) 제외한 별도 대시보드 생성
- `periodOverride: auto` 사용하면 긴 시간 범위에서 고해상도 데이터 쿼리 방지 → 비용 절감

## 3. 트러블슈팅
### 3.1 주요 이슈

**위젯에 "No data" 표시**
- 원인: Dimension 값 불일치, 시간 범위에 데이터 없음, 권한 부족
- 해결:
  ```bash
  # 실제 지표 Dimension 확인
  aws cloudwatch list-metrics \
    --namespace "AWS/ApplicationELB" \
    --metric-name "RequestCount" \
    --query 'Metrics[*].Dimensions'
  ```

**대시보드 로딩이 느린 경우**
- 원인: 위젯 수 과다, 짧은 Period에 긴 시간 범위 설정
- 해결: `periodOverride: auto` 설정, 로그 쿼리 위젯 수 최소화 (4개 이하 권장)

**Metric Math 위젯이 0만 표시**
- 원인: 기반 지표 단위 불일치 또는 데이터 타입 문제
- 해결: 각 기반 지표를 `"visible": true`로 임시 설정해 개별 확인

### 3.2 자주 발생하는 문제 (Q&A)

- Q: 대시보드를 IaC로 관리하면 콘솔에서 수정한 내용이 사라지나요?
- A: Terraform apply 시 덮어써짐. 콘솔 편집 후 반드시 `get-dashboard`로 JSON 복사해 코드 반영

- Q: 알람 위젯에서 특정 알람만 필터링하려면?
- A: 현재 필터 기능 없음. Composite Alarm 또는 알람 이름 접두사로 그룹화 필요

## 4. 모니터링 및 알람
```bash
# 대시보드 접근 감사 — CloudTrail
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=GetDashboard \
  --max-items 10

# 모든 대시보드 목록 및 최종 수정 시간
aws cloudwatch list-dashboards \
  --query 'sort_by(DashboardEntries, &LastModified)[-5:]'
```

## 5. TIP
- **대시보드 계층 설계**: 서비스 → 리소스 → 디버깅 3계층 구조, 위젯 간 링크로 드릴다운 연결
- 알람 상태 위젯을 대시보드 최상단에 배치 — 장애 시 스크롤 없이 바로 파악
- `annotations.horizontal`로 SLA 임계값 수평선 표시 — 정상/이상 기준 시각적 명확화
- 색상 코딩 통일: 정상 `#2ca02c`, 경고 `#ff7f0e`, 위험 `#d62728`
- 대시보드 URL 파라미터: `?start=-PT1H&end=PT0H` 형식으로 시간 범위 고정 링크 공유 가능
- 관련 문서: [CloudWatch 대시보드 공식 가이드](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Dashboards.html)
