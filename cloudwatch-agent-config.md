# CloudWatch Agent 설정

## 1. 개요
- CloudWatch Agent(CWAgent)는 EC2/온프레미스 서버에서 메모리, 디스크, 프로세스 등 기본 제공되지 않는 지표와 로그를 수집하는 에이전트
- 기본 EC2 지표(CPU, 네트워크)만으로는 메모리 부족, 디스크 포화 등 OS 레벨 문제 감지 불가 — CWAgent 필수
- EKS에서는 DaemonSet으로 배포해 노드 및 컨테이너 지표를 Container Insights로 전송

## 2. 설명
### 2.1 핵심 개념

**수집 데이터 유형**
| 유형 | 설명 | 기본 지표와 차이 |
|------|------|----------------|
| 메모리 | mem_used_percent, mem_available | EC2 기본 제공 안 됨 |
| 디스크 | disk_used_percent, disk_inodes_free | EC2 기본 제공 안 됨 |
| 프로세스 | procstat (PID별 CPU/메모리) | EC2 기본 제공 안 됨 |
| 네트워크 | netstat (TCP 연결 수) | EC2 기본 제공 안 됨 |
| 로그 파일 | 임의 로그 파일 → CloudWatch Logs | - |
| StatsD | UDP 8125 포트 커스텀 지표 수신 | - |
| collectd | collectd 프로토콜 지표 수신 | - |

**설정 파일 위치**
- Linux: `/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json`
- Windows: `C:\ProgramData\Amazon\AmazonCloudWatchAgent\amazon-cloudwatch-agent.json`
- SSM Parameter Store에 저장 후 원격 배포 가능 (권장)

### 2.2 실무 적용 코드

**기본 설정 파일 구조**
```json
{
  "agent": {
    "metrics_collection_interval": 60,
    "run_as_user": "cwagent",
    "logfile": "/opt/aws/amazon-cloudwatch-agent/logs/amazon-cloudwatch-agent.log"
  },
  "metrics": {
    "namespace": "Custom/EC2",
    "append_dimensions": {
      "AutoScalingGroupName": "${aws:AutoScalingGroupName}",
      "InstanceId": "${aws:InstanceId}",
      "InstanceType": "${aws:InstanceType}"
    },
    "metrics_collected": {
      "mem": {
        "measurement": [
          "mem_used_percent",
          "mem_available_percent",
          "mem_total",
          "mem_used"
        ],
        "metrics_collection_interval": 60
      },
      "disk": {
        "measurement": [
          "disk_used_percent",
          "disk_free",
          "disk_inodes_free"
        ],
        "metrics_collection_interval": 60,
        "resources": ["/", "/data"]
      },
      "netstat": {
        "measurement": [
          "tcp_established",
          "tcp_time_wait",
          "tcp_close_wait"
        ],
        "metrics_collection_interval": 60
      },
      "cpu": {
        "measurement": [
          "cpu_usage_idle",
          "cpu_usage_iowait",
          "cpu_usage_user",
          "cpu_usage_system"
        ],
        "metrics_collection_interval": 60,
        "totalcpu": true
      }
    },
    "aggregation_dimensions": [
      ["AutoScalingGroupName"],
      ["InstanceId", "InstanceType"],
      []
    ]
  },
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [
          {
            "file_path": "/var/log/messages",
            "log_group_name": "/ec2/system/messages",
            "log_stream_name": "{instance_id}",
            "retention_in_days": 14
          },
          {
            "file_path": "/var/log/app/application.log",
            "log_group_name": "/app/prod/application",
            "log_stream_name": "{instance_id}",
            "multi_line_start_pattern": "^\\d{4}-\\d{2}-\\d{2}",
            "retention_in_days": 30
          }
        ]
      }
    }
  }
}
```

**procstat — 특정 프로세스 모니터링**
```json
{
  "metrics": {
    "metrics_collected": {
      "procstat": [
        {
          "pid_file": "/var/run/nginx.pid",
          "measurement": [
            "cpu_usage",
            "memory_rss",
            "memory_vms",
            "num_threads",
            "read_bytes",
            "write_bytes"
          ],
          "metrics_collection_interval": 60
        },
        {
          "pattern": "java",
          "measurement": [
            "cpu_usage",
            "memory_rss",
            "num_fds"
          ]
        }
      ]
    }
  }
}
```

**EC2 설치 및 실행**
```bash
# Amazon Linux 2 / AL2023 설치
sudo yum install -y amazon-cloudwatch-agent

# 또는 직접 다운로드
wget https://s3.amazonaws.com/amazoncloudwatch-agent/amazon_linux/amd64/latest/amazon-cloudwatch-agent.rpm
sudo rpm -U amazon-cloudwatch-agent.rpm

# 설정 마법사 실행 (대화형)
sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-config-wizard

# 설정 파일로 직접 시작
sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a fetch-config \
  -m ec2 \
  -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json \
  -s

# 상태 확인
sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl -m ec2 -a status

# 서비스 재시작
sudo systemctl restart amazon-cloudwatch-agent
```

**SSM Parameter Store에 설정 저장 및 원격 적용**
```bash
# 설정 파일을 SSM에 저장
aws ssm put-parameter \
  --name "/cloudwatch-agent/config/prod-ec2" \
  --type String \
  --value file://amazon-cloudwatch-agent.json \
  --overwrite

# EC2에서 SSM 설정 적용
sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a fetch-config \
  -m ec2 \
  -c ssm:/cloudwatch-agent/config/prod-ec2 \
  -s

# Systems Manager Run Command로 다수 EC2에 일괄 적용
aws ssm send-command \
  --document-name "AmazonCloudWatch-ManageAgent" \
  --parameters '{"action":["configure"],"mode":["ec2"],"optionalConfigurationSource":["ssm"],"optionalConfigurationLocation":["/cloudwatch-agent/config/prod-ec2"],"optionalRestart":["yes"]}' \
  --targets "Key=tag:Environment,Values=prod" \
  --output text
```

**Terraform — IAM Role 및 EC2 설정**
```hcl
# CloudWatch Agent에 필요한 IAM Policy
resource "aws_iam_role_policy_attachment" "cw_agent" {
  role       = aws_iam_role.ec2_role.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
}

# SSM 접근 권한 (설정 파일 조회용)
resource "aws_iam_role_policy_attachment" "ssm_read" {
  role       = aws_iam_role.ec2_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMReadOnlyAccess"
}

# UserData로 자동 설치 및 설정
resource "aws_launch_template" "app" {
  name_prefix   = "prod-app-"
  image_id      = data.aws_ami.al2023.id
  instance_type = "t3.medium"

  iam_instance_profile {
    name = aws_iam_instance_profile.ec2.name
  }

  user_data = base64encode(<<-EOF
    #!/bin/bash
    yum install -y amazon-cloudwatch-agent
    /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
      -a fetch-config -m ec2 \
      -c ssm:/cloudwatch-agent/config/prod-ec2 -s
  EOF
  )
}
```

**EKS DaemonSet — Container Insights 설정**
```yaml
# CloudWatch Agent ConfigMap
apiVersion: v1
kind: ConfigMap
metadata:
  name: cwagentconfig
  namespace: amazon-cloudwatch
data:
  cwagentconfig.json: |
    {
      "logs": {
        "metrics_collected": {
          "kubernetes": {
            "cluster_name": "prod-cluster",
            "metrics_collection_interval": 60
          }
        },
        "force_flush_interval": 5
      }
    }
---
# DaemonSet
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: cloudwatch-agent
  namespace: amazon-cloudwatch
spec:
  selector:
    matchLabels:
      name: cloudwatch-agent
  template:
    metadata:
      labels:
        name: cloudwatch-agent
    spec:
      serviceAccountName: cloudwatch-agent
      containers:
        - name: cloudwatch-agent
          image: amazon/cloudwatch-agent:1.300040.0b650
          resources:
            limits:
              cpu: 200m
              memory: 200Mi
            requests:
              cpu: 200m
              memory: 200Mi
          volumeMounts:
            - name: cwagentconfig
              mountPath: /etc/cwagentconfig
            - name: rootfs
              mountPath: /rootfs
              readOnly: true
            - name: dockersock
              mountPath: /var/run/docker.sock
              readOnly: true
            - name: varlibdocker
              mountPath: /var/lib/docker
              readOnly: true
            - name: containerdsock
              mountPath: /run/containerd/containerd.sock
              readOnly: true
            - name: sys
              mountPath: /sys
              readOnly: true
            - name: devdisk
              mountPath: /dev/disk
              readOnly: true
      volumes:
        - name: cwagentconfig
          configMap:
            name: cwagentconfig
        - name: rootfs
          hostPath:
            path: /
        - name: dockersock
          hostPath:
            path: /var/run/docker.sock
        - name: varlibdocker
          hostPath:
            path: /var/lib/docker
        - name: containerdsock
          hostPath:
            path: /run/containerd/containerd.sock
        - name: sys
          hostPath:
            path: /sys
        - name: devdisk
          hostPath:
            path: /dev/disk
      tolerations:
        - key: node-role.kubernetes.io/master
          effect: NoSchedule
```

**EKS add-on으로 간편 설치 (권장)**
```bash
# CloudWatch Observability EKS Add-on 설치
aws eks create-addon \
  --cluster-name prod-cluster \
  --addon-name amazon-cloudwatch-observability \
  --service-account-role-arn arn:aws:iam::123456789012:role/EKS-CWAgent-Role
```

### 2.3 보안/비용 Best Practice
- **최소 권한 IAM**: `CloudWatchAgentServerPolicy` 사용, 추가 권한 최소화
- **metrics_collection_interval**: 기본 60초 유지 — 10초로 줄이면 비용 6배 증가
- **aggregation_dimensions**: ASG 단위로 집계 설정 시 스케일 인/아웃에도 지표 연속성 유지
- 로그 그룹에 `retention_in_days` 필수 설정 — 미설정 시 영구 보관으로 비용 누적
- 디스크 `resources`는 실제 마운트 포인트만 지정 — tmpfs, proc 등 제외

## 3. 트러블슈팅
### 3.1 주요 이슈

**에이전트 시작 실패**
- 증상: `systemctl status amazon-cloudwatch-agent` → failed
- 원인: JSON 설정 파일 문법 오류
- 해결:
  ```bash
  # 설정 파일 문법 검증
  sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
    -a fetch-config -m ec2 \
    -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json

  # 에이전트 로그 확인
  sudo tail -100 /opt/aws/amazon-cloudwatch-agent/logs/amazon-cloudwatch-agent.log
  ```

**지표가 CloudWatch에 나타나지 않음**
- 증상: 에이전트는 실행 중이나 콘솔에서 지표 없음
- 원인: IAM 권한 부족 또는 IMDSv2 관련 설정
- 해결:
  ```bash
  # IAM 권한 확인
  aws iam simulate-principal-policy \
    --policy-source-arn arn:aws:iam::123456789012:role/ec2-role \
    --action-names cloudwatch:PutMetricData

  # 에이전트 로그에서 권한 오류 확인
  grep -i "error\|permission\|denied" /opt/aws/amazon-cloudwatch-agent/logs/amazon-cloudwatch-agent.log
  ```

**메모리 지표 단위 이상 (바이트 vs 퍼센트)**
- 원인: measurement 배열에 `mem_used` (바이트)와 `mem_used_percent` 혼재
- 해결: 알람 생성 시 단위 명시 확인. 퍼센트 알람은 `mem_used_percent`만 사용

### 3.2 자주 발생하는 문제 (Q&A)

- Q: 온프레미스 서버에서도 CWAgent를 사용할 수 있나요?
- A: 가능. `-m onPremise` 모드로 실행. IAM 사용자 자격증명 또는 AWS IAM Anywhere 사용

- Q: procstat에서 프로세스를 찾지 못할 때?
- A: `pid_file` 대신 `pattern` (프로세스명 정규식) 또는 `exe` (실행 파일 경로) 사용

- Q: 여러 설정 파일을 합칠 수 있나요?
- A: `-c` 옵션에 여러 설정 소스 지정 가능. 설정이 merge됨

## 4. 모니터링 및 알람
```hcl
# 메모리 사용률 알람
resource "aws_cloudwatch_metric_alarm" "memory_high" {
  alarm_name          = "ec2-memory-usage-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "mem_used_percent"
  namespace           = "Custom/EC2"
  period              = 60
  statistic           = "Average"
  threshold           = 85
  alarm_description   = "EC2 메모리 사용률 85% 초과"
  alarm_actions       = [aws_sns_topic.ops.arn]
  dimensions = {
    AutoScalingGroupName = "prod-api-asg"
  }
}

# 디스크 사용률 알람
resource "aws_cloudwatch_metric_alarm" "disk_high" {
  alarm_name          = "ec2-disk-usage-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "disk_used_percent"
  namespace           = "Custom/EC2"
  period              = 300
  statistic           = "Maximum"
  threshold           = 80
  alarm_actions       = [aws_sns_topic.ops.arn]
  dimensions = {
    path         = "/"
    InstanceId   = "*"  # 와일드카드 불가 — 실제 InstanceId 또는 ASG 차원 사용
  }
}
```

## 5. TIP
- **wizard 활용**: 처음 설정할 때 `amazon-cloudwatch-agent-config-wizard`로 대화형 설정 생성 후 SSM에 저장
- **Packer/EC2 Image Builder**: AMI 빌드 시 CWAgent 사전 설치하여 인스턴스 기동 즉시 수집 시작
- `append_dimensions`에 `AutoScalingGroupName`을 추가하면 대시보드에서 ASG 단위 집계 가능
- EKS에서는 개별 DaemonSet 대신 `amazon-cloudwatch-observability` Add-on 사용 권장 (관리 간편)
- 관련 문서: [CloudWatch Agent 설정 스키마](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-Agent-Configuration-File-Details.html)
