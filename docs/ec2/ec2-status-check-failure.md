# EC2 상태 체크 실패 대응

## 1. 개요

EC2 상태 체크(Status Check)는 실행 중인 인스턴스에 AWS가 1분마다 수행하는 기본 진단이다. 하나라도 실패하면 콘솔 상태는 `impaired`가 된다. 인스턴스가 `running`이어도 애플리케이션과 SSH가 모두 불가능할 수 있으므로, 인스턴스 상태만으로 정상 여부를 판단하면 안 된다.

대응의 첫 단계는 실패한 항목을 구분하는 것이다. `System status check`은 AWS 호스트·전원·네트워크 문제이며, `Instance status check`은 게스트 OS·네트워크·파일시스템 문제다. 자동 복구(Automatic Instance Recovery)는 **System status check 실패에만** 동작한다.

---

## 2. 설명

### 2.1 상태 체크 유형과 즉시 조치

| 상태 체크 / CloudWatch 지표 | 의미 | 대표 원인 | 우선 조치 |
|---|---|---|---|
| System / `StatusCheckFailed_System` | 인스턴스가 실행 중인 AWS 인프라 상태 | 물리 호스트 장애, 호스트 네트워크·전원·소프트웨어 문제 | 자동 복구 상태 확인 → 실패 지속 시 EBS 인스턴스 Stop/Start |
| Instance / `StatusCheckFailed_Instance` | 게스트 OS와 ENI까지의 도달성 | 커널 패닉, OOM, 잘못된 `fstab`, 디스크·파일시스템 오류, OS 방화벽·네트워크 설정 | 콘솔 로그·스크린샷 수집 → Reboot → 구조 복구 또는 인스턴스 교체 |
| Attached EBS / `StatusCheckFailed_AttachedEBS` | 연결된 EBS 볼륨의 I/O 상태 | EBS 성능·연결 장애, 볼륨 오류 | 영향 볼륨 식별 → 애플리케이션 격리·백업 → 볼륨 복구 또는 인스턴스 교체 |

`StatusCheckFailed`는 System과 Instance 상태 체크의 합계 성격의 지표다. 원인과 자동 조치 여부는 합계가 아닌 개별 지표로 판단한다.

### 2.2 장애 대응 흐름

```
CloudWatch Alarm 또는 EC2 Status Check = impaired
  → System / Instance / Attached EBS 중 실패 항목 확인
  ├─ System
  │    → 자동 복구 이벤트 확인
  │    → 정상화 실패 시 Stop/Start (EBS-backed)
  ├─ Instance
  │    → Console log·screenshot 수집
  │    → Reboot 1회
  │    → 여전히 실패: SSM/Serial Console 또는 루트 볼륨 구조 복구
  └─ Attached EBS
       → 해당 볼륨과 의존 서비스 식별
       → 데이터 일관성 확보 후 백업·교체 또는 새 인스턴스 전환
```

프로덕션에서 단일 인스턴스의 복구를 기다리는 동안에는 먼저 ALB/NLB Target Group에서 해당 인스턴스가 `unhealthy`인지 확인한다. 여러 AZ의 Auto Scaling Group(ASG)과 로드밸런서가 있다면 해당 인스턴스의 복구보다 정상 인스턴스로의 트래픽 우회와 용량 유지가 우선이다.

### 2.3 공통: 증거 수집과 영향 확인

복구 명령을 실행하기 전에 상태와 로그를 확보한다. `reboot-instances`와 Stop/Start는 문제의 원인이 되는 메모리 상태와 일부 로그를 잃게 할 수 있다.

```bash
# 상태 체크, 예정 이벤트, 인스턴스 상태를 함께 조회한다.
aws ec2 describe-instance-status \
  --instance-ids <INSTANCE_ID> \
  --include-all-instances \
  --region ap-northeast-2 \
  --output json

# 커널 패닉, fsck, fstab, OOM 등의 부팅·시스템 로그를 조회한다.
aws ec2 get-console-output \
  --instance-id <INSTANCE_ID> \
  --latest \
  --region ap-northeast-2 \
  --output json

# 화면에 멈춘 부팅 오류나 kernel panic 메시지를 확인한다.
aws ec2 get-console-screenshot \
  --instance-id <INSTANCE_ID> \
  --wake-up \
  --region ap-northeast-2 \
  --output json

# ALB Target Group에서 이미 트래픽이 제외됐는지 확인한다.
aws elbv2 describe-target-health \
  --target-group-arn <TARGET_GROUP_ARN> \
  --targets Id=<INSTANCE_ID> \
  --region ap-northeast-2 \
  --output json
```

`get-console-screenshot`은 지원되는 인스턴스 유형에서만 사용한다. SSM Agent가 연결된 인스턴스는 SSH 대신 Session Manager로 접속해 `journalctl -b`, `df -h`, `free -m`, `ip route`, `systemctl --failed`를 먼저 확인한다.

### 2.4 System status check 실패 대응

System 실패는 게스트 OS 설정 문제가 아니라 AWS 호스트 문제다. 지원 인스턴스에서는 단순 자동 복구(Simplified Automatic Recovery)가 기본 활성화되어 있으며, CloudWatch action 기반 복구도 사전에 설정할 수 있다. 복구가 성공하면 동일한 인스턴스 ID, private/public IP, Elastic IP, EBS 볼륨, AZ가 유지되지만 RAM 데이터와 OS uptime은 사라진다.

#### 1) 자동 복구 이벤트 확인

AWS Health Dashboard에서 다음 이벤트를 확인한다.

| 복구 방식 | 성공 이벤트 | 실패 이벤트 |
|---|---|---|
| Simplified Automatic Recovery | `AWS_EC2_SIMPLIFIED_AUTO_RECOVERY_SUCCESS` | `AWS_EC2_SIMPLIFIED_AUTO_RECOVERY_FAILURE` |
| CloudWatch action 기반 | `AWS_EC2_INSTANCE_AUTO_RECOVERY_SUCCESS` | `AWS_EC2_INSTANCE_AUTO_RECOVERY_FAILURE` |

자동 복구가 실패했거나 System 상태가 계속 `impaired`이면 수동으로 Stop/Start한다. EBS-backed 인스턴스는 Start 시 일반적으로 새 호스트로 이동한다. 단순 Reboot는 같은 호스트에서 OS만 재시작하므로 호스트 장애 해결책이 아니다.

```bash
# 중지 전 인스턴스 스토어와 임시 데이터의 백업 필요 여부를 확인한다.
aws ec2 describe-instances \
  --instance-ids <INSTANCE_ID> \
  --query 'Reservations[0].Instances[0].BlockDeviceMappings' \
  --region ap-northeast-2 \
  --output json

# EBS-backed 인스턴스를 중지하고 완전히 멈출 때까지 기다린다.
aws ec2 stop-instances \
  --instance-ids <INSTANCE_ID> \
  --region ap-northeast-2 \
  --output json
aws ec2 wait instance-stopped \
  --instance-ids <INSTANCE_ID> \
  --region ap-northeast-2

# 새 호스트 배치를 위해 인스턴스를 다시 시작한다.
aws ec2 start-instances \
  --instance-ids <INSTANCE_ID> \
  --region ap-northeast-2 \
  --output json
aws ec2 wait instance-running \
  --instance-ids <INSTANCE_ID> \
  --region ap-northeast-2
```

> **주의**: Stop/Start하면 인스턴스 스토어 데이터는 삭제된다. Elastic IP가 아닌 자동 할당 public IPv4도 바뀔 수 있다. ASG 멤버를 임의로 Stop하면 ASG가 종료·교체할 수 있으므로, ASG에서는 새 인스턴스 교체와 Target Group 정상화 절차를 우선 적용한다.

### 2.5 Instance status check 실패 대응

Instance 실패는 EC2가 ENI에 ARP 요청을 보냈을 때 응답하지 못하는 상태를 포함한다. OS가 멈췄거나, 커널·파일시스템·네트워크 설정이 손상됐을 수 있다. 먼저 1회 Reboot한 뒤 상태 체크가 회복되는지 확인한다.

```bash
# 게스트 OS를 재부팅한다. EBS와 인스턴스 스토어는 유지되지만 RAM 데이터는 사라진다.
aws ec2 reboot-instances \
  --instance-ids <INSTANCE_ID> \
  --region ap-northeast-2 \
  --output json

# 5분 이내 상태 체크 회복 여부를 반복 조회한다.
aws ec2 describe-instance-status \
  --instance-ids <INSTANCE_ID> \
  --region ap-northeast-2 \
  --output json
```

Reboot 뒤에도 실패하면 복구 방식은 원인에 따라 나눈다.

| 로그·증상 | 우선 복구 | 다음 조치 |
|---|---|---|
| `Out of memory`, kernel panic | SSM/Serial Console로 최근 배포·메모리 설정 확인 | 원인 프로세스·설정 롤백 후 재부팅, ASG라면 새 인스턴스 교체 |
| `VFS: Unable to mount root fs`, `fsck`, 잘못된 `fstab` | 루트 볼륨을 구조용 인스턴스에 연결 | `fstab` UUID 수정, unmount 상태에서 파일시스템 검사 |
| `I/O error`, EBS 관련 오류 | 영향 볼륨 스냅샷과 EBS 상태 확인 | 복구용 볼륨·인스턴스로 교체, 애플리케이션 데이터 정합성 검사 |
| NIC/route/iptables 변경 직후 접속 불가 | EC2 Serial Console 또는 SSM으로 네트워크 설정 복구 | 보안 그룹·NACL·route table도 함께 검증 |

루트 파일시스템을 직접 수리해야 하면 인스턴스를 Stop하고 루트 EBS를 같은 AZ의 구조용 EC2에 데이터 볼륨으로 Attach한다. 구조용 인스턴스에서 `/etc/fstab`과 부팅 로그를 확인하고, 파일시스템 검사는 대상 볼륨이 unmount된 상태에서만 실행한다. 상세 절차는 [EBS 스냅샷으로 루트 볼륨 복구](ec2-snapshot-root-volume-recovery.md)를 따른다.

### 2.6 Attached EBS status check 실패 대응

Attached EBS 실패는 애플리케이션 I/O 오류, filesystem read-only 전환, `dmesg`의 block I/O error로 나타날 수 있다. 루트 볼륨인지 데이터 볼륨인지와 해당 볼륨을 사용하는 서비스부터 확인한다.

```bash
# 인스턴스에 연결된 EBS와 디바이스 매핑을 확인한다.
aws ec2 describe-volumes \
  --filters Name=attachment.instance-id,Values=<INSTANCE_ID> \
  --region ap-northeast-2 \
  --output json

# Linux에서 커널이 기록한 EBS·파일시스템 I/O 오류를 확인한다.
sudo dmesg -T | grep -Ei 'I/O error|blk_update_request|nvme|xfs|ext4'

# 마운트별 파일시스템과 read-only 여부를 확인한다.
findmnt -o TARGET,SOURCE,FSTYPE,OPTIONS
```

데이터베이스와 같이 쓰기 일관성이 중요한 서비스는 즉시 쓰기를 중지하거나 failover한 뒤 스냅샷·복구를 진행한다. 오류 EBS를 강제로 Detach하지 않는다. 인스턴스 중지 또는 서비스 unmount 후에만 볼륨을 분리하며, 복구 후에는 애플리케이션 수준의 데이터 정합성 검사를 수행한다.

---

## 3. 트러블슈팅

### 3.1 System 상태 체크가 계속 실패

#### 원인

물리 호스트의 네트워크·전원·하드웨어 또는 호스트 소프트웨어 문제다. 게스트 OS 재부팅만으로는 호스트가 바뀌지 않는다.

#### 해결 방법

1. AWS Health Dashboard의 자동 복구 성공·실패 이벤트를 확인한다.
2. CloudWatch action 기반 복구가 이미 설정됐으면 복구 완료까지 상태를 관찰한다.
3. 실패가 지속되면 인스턴스 스토어 백업 여부를 확인한 뒤 EBS-backed 인스턴스를 Stop/Start한다.
4. Start가 용량 부족으로 실패하거나 상태가 회복되지 않으면 AWS Support Case를 생성하고, 스냅샷·AMI로 같은 AZ 또는 다른 AZ에 대체 인스턴스를 기동한다.

### 3.2 Instance 상태 체크는 실패하지만 System은 정상

#### 원인

인스턴스 내부의 OS, 커널, 부팅 파일시스템, 메모리 또는 네트워크 설정 문제다. 자동 인스턴스 복구 대상이 아니다.

#### 해결 방법

1. Console output과 screenshot을 확보한다.
2. Reboot를 한 번 수행하고 `describe-instance-status`로 회복을 확인한다.
3. 회복하지 않으면 SSM 또는 EC2 Serial Console에서 오류를 수리한다.
4. 접속 불가·루트 파일시스템 오류면 루트 EBS 구조 또는 새 인스턴스 교체로 전환한다.

### 3.3 상태 체크는 정상인데 서비스만 다운

#### 원인

EC2 상태 체크는 애플리케이션, HTTP 응답, Target Group health check를 보장하지 않는다.

#### 해결 방법

ALB/NLB Target Group health check, 애플리케이션 `/health`, 프로세스와 포트 리스닝 상태를 별도로 확인한다. 서비스 장애에 EC2 `StatusCheckFailed` alarm만 사용하면 탐지하지 못한다.

---

## 4. 모니터링 및 알람

System 상태 체크에는 복구 액션을, Instance 상태 체크에는 SNS·PagerDuty 등의 알림 액션을 연결한다. Instance 상태 체크에 `recover` 액션을 연결해도 자동 복구되지 않는다.

```hcl
# System status check 실패 시 알림과 EC2 자동 복구를 함께 실행
resource "aws_cloudwatch_metric_alarm" "ec2_system_status_failed" {
  alarm_name          = "prod-ec2-system-status-failed"
  alarm_description   = "EC2 System status check 실패: 자동 복구와 운영자 알림 실행"
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed_System"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    InstanceId = aws_instance.app.id
  }

  alarm_actions = [
    "arn:aws:automate:ap-northeast-2:ec2:recover",
    aws_sns_topic.ops_alert.arn,
  ]
}

# OS·파일시스템 문제는 자동 복구 대신 운영자에게 알림
resource "aws_cloudwatch_metric_alarm" "ec2_instance_status_failed" {
  alarm_name          = "prod-ec2-instance-status-failed"
  alarm_description   = "EC2 Instance status check 실패: OS와 네트워크 진단 필요"
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed_Instance"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    InstanceId = aws_instance.app.id
  }

  alarm_actions = [aws_sns_topic.ops_alert.arn]
}
```

| 확인 항목 | 정상 기준 | 대응 기준 |
|---|---|---|
| `StatusCheckFailed_System` | 0 | 2분 연속 1이면 자동 복구 및 알림 |
| `StatusCheckFailed_Instance` | 0 | 2분 연속 1이면 OS 장애 런북 시작 |
| `StatusCheckFailed_AttachedEBS` | 0 | 1분이라도 1이면 영향 볼륨·서비스 확인 |
| ALB `HealthyHostCount` | 서비스 최소 용량 이상 | 정상 타겟 수가 최소 용량 아래면 트래픽 우회·ASG 교체 |

---

## 5. TIP

- 단일 EC2의 `recover` alarm은 가용성을 보완할 뿐 고가용성 구조를 대체하지 않는다. 프로덕션은 최소 2개 AZ, ALB/NLB, ASG로 트래픽 failover와 인스턴스 교체를 구성한다.
- `recover` 액션은 지원되는 인스턴스에서 System 상태 체크가 실패할 때만 사용한다. 인스턴스 스토어 데이터와 RAM 데이터는 복구 전제에 포함하지 않는다.
- ASG 인스턴스는 수동 복구보다 새 Golden AMI 기반 교체가 더 안전한 경우가 많다. 상태가 있는 단일 인스턴스는 EBS 스냅샷, 복구 시간 목표(RTO), 데이터 정합성 절차를 사전에 검증한다.
- 관련 문서: [물리 호스트 변경](ec2-physical-host-change.md), [EBS 스냅샷으로 루트 볼륨 복구](ec2-snapshot-root-volume-recovery.md), [CodeDeploy 로드밸런서 처리](../platform/aws-codedeploy.md)
- 참고: [EC2 status checks](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/monitoring-system-instance-status-check.html), [Automatic instance recovery](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-instance-recover.html), [Linux status check troubleshooting](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/TroubleshootingInstances.html)
