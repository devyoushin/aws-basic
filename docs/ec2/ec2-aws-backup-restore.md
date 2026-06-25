# AWS Backup으로 EC2 인스턴스 백업 및 복구

## 1. 개요

AWS Backup은 EC2 인스턴스의 백업 정책, 보관 주기, 복구 지점을 중앙에서 관리하는 서비스다. EC2 복구 시 AWS Backup은 복구 지점(Recovery point)을 기반으로 AMI(Amazon Machine Image), 새 EC2 인스턴스, 루트 EBS 볼륨, 데이터 EBS 볼륨, EBS 스냅샷을 생성한다.

AMI를 직접 생성하는 방식은 단일 인스턴스의 수동 복구에 적합하고, AWS Backup은 여러 인스턴스의 백업 주기·보관·감사·교차 리전 복사까지 정책화해야 할 때 적합하다. 복구 결과는 기존 인스턴스의 수리가 아니라 **새 EC2 인스턴스 생성**이므로, 네트워크·IAM·트래픽 전환 절차를 함께 준비해야 함.

---

## 2. 설명

### 2.1 AWS Backup EC2 복구 범위

| 항목 | 백업/복구 여부 | 운영 시 주의점 |
|---|---|---|
| 루트 EBS 볼륨 | 포함 | 복구 시 새 루트 볼륨 생성 |
| 데이터 EBS 볼륨 | 포함 | 원본 인스턴스에 연결된 EBS 데이터 볼륨도 새 볼륨으로 복구 |
| AMI | 복구 과정에서 생성 | 복구 작업의 중간 산출물로 사용됨 |
| instance ID | 미포함 | 복구 결과는 새 instance ID |
| primary ENI, primary private IP | 미포함 | 새 primary ENI가 생성됨. 기존 private IP 의존 구조는 전환 설계 필요 |
| Elastic IP | 연결 상태 미복구 | 복구 후 새 인스턴스에 재연결 |
| UserData | 미복구 | Launch Template, IaC, SSM 문서로 별도 관리 |
| key pair | 원본 key pair 사용 | 복구 작업 중 다른 key pair로 변경 불가 |
| IAM instance profile | 선택 가능 | 원본 profile 사용 시 `iam:PassRole` 권한 필요 |
| Security Group, Subnet | 기본값 제안, 변경 가능 | 복구 대상 VPC·Subnet·SG를 명시적으로 검토 |

AWS Backup은 EC2의 볼륨과 일부 인스턴스 설정을 복구하지만, 애플리케이션의 외부 의존성까지 복구하지 않는다. RDS, EFS, S3, Route 53, ALB Target Group, SSM Parameter Store, Secrets Manager는 각각 별도 복구 전략이 필요함.

### 2.2 백업 정책 설계

| 설계 항목 | 권장 기준 | 이유 |
|---|---|---|
| Backup Vault | 환경·서비스별 분리 | 운영/개발, 중요도별 보관 정책 분리 |
| Backup Plan | 태그 기반 할당 | 신규 EC2 누락 방지 |
| 보관 기간 | RPO/RTO와 감사 기준에 맞춤 | 불필요한 장기 보관 비용 방지 |
| 교차 리전 복사 | 리전 장애 대비 서비스에 적용 | 서울 리전 장애 시 대상 리전에서 복구 |
| Vault Lock | 삭제 방지 요구가 있는 백업에 적용 | 랜섬웨어·오조작 삭제 방지 |
| 복구 테스트 | 정기적으로 별도 Subnet에 복구 | 백업 성공과 복구 가능성은 별개 |

태그 기반 백업 예시는 아래 기준처럼 단순하게 유지한다.

| 태그 | 값 | 의미 |
|---|---|---|
| `Backup` | `daily` | 매일 백업 대상 |
| `Backup` | `critical` | 짧은 RPO, 장기 보관, 교차 리전 복사 대상 |
| `Service` | `<SERVICE_NAME>` | 복구 우선순위와 담당팀 식별 |
| `Environment` | `prod` | 운영 백업 정책 적용 |

### 2.3 Recovery point 확인

복구는 Backup Vault에 저장된 복구 지점(Recovery point)을 선택하는 것부터 시작한다.

```bash
# EC2 리소스의 복구 지점을 조회한다.
aws backup list-recovery-points-by-resource \
  --resource-arn arn:aws:ec2:ap-northeast-2:123456789012:instance/<INSTANCE_ID> \
  --region ap-northeast-2 \
  --output json

# Backup Vault 기준으로 EC2 복구 지점을 조회한다.
aws backup list-recovery-points-by-backup-vault \
  --backup-vault-name <BACKUP_VAULT_NAME> \
  --by-resource-type EC2 \
  --region ap-northeast-2 \
  --output json
```

복구 전에는 선택한 복구 지점의 생성 시각, 원본 instance ID, 암호화 KMS key, 대상 리전 복사 여부를 확인한다.

```bash
# 복구 지점 상세를 확인한다.
aws backup describe-recovery-point \
  --backup-vault-name <BACKUP_VAULT_NAME> \
  --recovery-point-arn <RECOVERY_POINT_ARN> \
  --region ap-northeast-2 \
  --output json
```

### 2.4 복구 메타데이터 확인

CLI 복구는 `get-recovery-point-restore-metadata`로 원본 백업 시점의 설정을 먼저 조회한 뒤, 필요한 값을 수정해 `start-restore-job`에 전달하는 방식으로 진행한다.

```bash
# 복구에 사용할 원본 메타데이터를 조회한다.
aws backup get-recovery-point-restore-metadata \
  --backup-vault-name <BACKUP_VAULT_NAME> \
  --recovery-point-arn <RECOVERY_POINT_ARN> \
  --region ap-northeast-2 \
  --output json
```

검토해야 할 주요 메타데이터는 아래와 같음.

| 메타데이터 | 확인 내용 |
|---|---|
| `InstanceType` | 복구 대상 AZ에서 해당 타입 용량 확보 가능 여부 |
| `SubnetId` | 격리 복구용 Subnet 또는 운영 Subnet 선택 |
| `SecurityGroupIds` | 운영 접근·헬스체크·SSM 접근 허용 여부 |
| `IamInstanceProfileName` | 원본 profile 재사용 여부와 `iam:PassRole` 권한 |
| `KeyName` | 원본 key pair 사용 가능 여부 |
| `BlockDeviceMappings` | 볼륨 크기, 타입, 암호화, KMS key |
| `RequireIMDSv2` | IMDSv2 필수화 여부 |

### 2.5 AWS Backup으로 EC2 복구 실행

복구 역할(Restore role)은 AWS Backup이 EC2, EBS, IAM instance profile 등을 생성하거나 연결할 수 있는 권한을 가진 IAM role이어야 한다. 원본 instance profile을 복구 인스턴스에 그대로 붙일 경우, 복구 역할에 해당 role에 대한 `iam:PassRole` 권한이 필요함.

```bash
# 복구 작업을 시작한다. Metadata는 실제 조회값을 기준으로 필요한 항목만 조정한다.
aws backup start-restore-job \
  --recovery-point-arn <RECOVERY_POINT_ARN> \
  --iam-role-arn arn:aws:iam::123456789012:role/<AWS_BACKUP_RESTORE_ROLE_NAME> \
  --resource-type EC2 \
  --metadata '{
    "InstanceType": "t3.medium",
    "SubnetId": "subnet-0123456789abcdef0",
    "SecurityGroupIds": "[\"sg-0123456789abcdef0\"]",
    "IamInstanceProfileName": "prod-app-instance-profile",
    "KeyName": "prod-app-key",
    "RequireIMDSv2": "true"
  }' \
  --copy-source-tags-to-restored-resource \
  --region ap-northeast-2 \
  --output json

# 복구 작업 상태를 확인한다.
aws backup describe-restore-job \
  --restore-job-id <RESTORE_JOB_ID> \
  --region ap-northeast-2 \
  --output json
```

복구가 완료되면 `describe-restore-job`의 `CreatedResourceArn`에서 새 EC2 인스턴스 ARN을 확인한다. ARN 끝의 `instance/<INSTANCE_ID>`가 복구 인스턴스 ID다. 이후 상태 체크, SSM 접속, 애플리케이션 헬스체크를 순서대로 검증한다.

```bash
# 복구 작업이 생성한 EC2 리소스 ARN을 확인한다.
aws backup describe-restore-job \
  --restore-job-id <RESTORE_JOB_ID> \
  --query '{Status:Status,StatusMessage:StatusMessage,CreatedResourceArn:CreatedResourceArn}' \
  --region ap-northeast-2 \
  --output json

# EC2 상태 체크를 확인한다.
aws ec2 describe-instance-status \
  --instance-ids <RESTORED_INSTANCE_ID> \
  --region ap-northeast-2 \
  --output json
```

### 2.6 복구 후 트래픽 전환

복구 인스턴스를 바로 운영 트래픽에 붙이지 않는다. 먼저 격리된 경로에서 OS, 디스크 마운트, 서비스, 로그, 보안 에이전트, 애플리케이션 헬스체크를 검증한다.

```bash
# SSM으로 기본 상태를 확인한다.
aws ssm send-command \
  --instance-ids <RESTORED_INSTANCE_ID> \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["systemctl --failed","df -h","lsblk","curl -fsS http://localhost:<APPLICATION_PORT>/health"]' \
  --region ap-northeast-2 \
  --output json

# ALB Target Group에 복구 인스턴스를 등록한다.
aws elbv2 register-targets \
  --target-group-arn <TARGET_GROUP_ARN> \
  --targets Id=<RESTORED_INSTANCE_ID>,Port=<APPLICATION_PORT> \
  --region ap-northeast-2 \
  --output json

# Target Group health check가 healthy가 될 때까지 기다린다.
aws elbv2 wait target-in-service \
  --target-group-arn <TARGET_GROUP_ARN> \
  --targets Id=<RESTORED_INSTANCE_ID>,Port=<APPLICATION_PORT> \
  --region ap-northeast-2 \
  --output json
```

단일 인스턴스가 Elastic IP(Elastic IP address)를 사용한다면 검증 후 EIP를 새 인스턴스에 재연결한다.

```bash
aws ec2 associate-address \
  --allocation-id <EIP_ALLOCATION_ID> \
  --instance-id <RESTORED_INSTANCE_ID> \
  --allow-reassociation \
  --region ap-northeast-2 \
  --output json
```

### 2.7 교차 리전·교차 계정 복구 주의사항

| 상황 | 주의사항 | 해결 |
|---|---|---|
| 교차 리전 복구 | 원본 EBS가 단일 리전 KMS key로 암호화됨 | 대상 리전 KMS key로 `BlockDeviceMappings`의 KMS key를 override |
| 교차 계정 복구 | 공유받은 계정이 원본 KMS key에 접근 불가 | KMS key policy 또는 grant 부여 |
| 미암호화 원본 볼륨 | 공유·복사 정책에서 실패 가능 | 복구 시 암호화 활성화 및 대상 KMS key 지정 |
| 원본 IAM profile 재사용 | 복구 역할에 `iam:PassRole` 부족 | 복구 역할 정책에 해당 role ARN 허용 |
| 원본 Subnet 미존재 | 다른 계정·리전에 동일 Subnet ID 없음 | 대상 환경의 SubnetId, SG를 명시적으로 지정 |

---

## 3. 트러블슈팅

### 증상
- `start-restore-job`가 `AccessDeniedException` 또는 `iam:PassRole` 오류로 실패함

### 원인
- 복구 인스턴스에 원본 IAM instance profile을 붙이려 하지만 AWS Backup 복구 역할에 해당 role을 전달할 권한이 없음

### 해결 방법

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowPassOriginalInstanceProfileRole",
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": "arn:aws:iam::123456789012:role/<EC2_INSTANCE_PROFILE_ROLE_NAME>"
    }
  ]
}
```

---

### 증상
- 교차 리전 또는 교차 계정 EC2 복구가 KMS 관련 오류로 실패함

### 원인
- 원본 볼륨 암호화에 사용된 KMS key가 대상 리전 또는 대상 계정에서 사용 불가능함

### 해결 방법

```bash
# 대상 리전에서 사용할 KMS key를 확인한다.
aws kms describe-key \
  --key-id <DESTINATION_KMS_KEY_ID> \
  --region ap-northeast-2 \
  --output json
```

`start-restore-job`의 `BlockDeviceMappings` 메타데이터에서 `Encrypted=true`, `KmsKeyId=<DESTINATION_KMS_KEY_ARN>`을 명시한다. 교차 계정이면 원본 계정 KMS key policy 또는 grant도 함께 확인한다.

---

### 증상
- 복구 인스턴스는 생성됐지만 애플리케이션이 부팅되지 않음

### 원인
- UserData가 복구되지 않았거나, 원본 Subnet/Security Group과 다른 네트워크에서 애플리케이션 의존성에 접근하지 못함

### 해결 방법

```bash
# cloud-init과 서비스 상태를 확인한다.
aws ssm send-command \
  --instance-ids <RESTORED_INSTANCE_ID> \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["sudo cloud-init status --long || true","systemctl --failed","journalctl -xe --no-pager | tail -200"]' \
  --region ap-northeast-2 \
  --output json
```

UserData는 AWS Backup EC2 복구 범위에 포함되지 않는다. Launch Template, SSM State Manager, Ansible, Terraform 등으로 부팅 후 설정을 재적용한다.

---

### 증상
- 복구 인스턴스가 ALB Target Group에서 unhealthy 상태임

### 원인
- Security Group에서 ALB → EC2 포트를 허용하지 않거나, 헬스체크 경로가 복구 시점의 애플리케이션 상태와 맞지 않음

### 해결 방법

```bash
aws elbv2 describe-target-health \
  --target-group-arn <TARGET_GROUP_ARN> \
  --targets Id=<RESTORED_INSTANCE_ID>,Port=<APPLICATION_PORT> \
  --region ap-northeast-2 \
  --output json
```

ALB Security Group을 소스로 하는 인바운드 규칙, 애플리케이션 리스닝 포트, 헬스체크 path, 응답 코드를 확인한다.

---

## 4. 모니터링 및 알람

### 4.1 백업·복구 작업 모니터링

| 대상 | 확인 항목 | 목적 |
|---|---|---|
| Backup Job | `State`, `StatusMessage`, `CompletionDate` | 백업 실패 탐지 |
| Restore Job | `Status`, `StatusMessage`, `CreatedResourceArn` | 복구 성공 및 생성 리소스 확인 |
| Recovery Point | `CreationDate`, `Lifecycle`, `CalculatedLifecycle` | 보관 정책 적용 확인 |
| EC2 상태 체크 | `StatusCheckFailed`, `StatusCheckFailed_System`, `StatusCheckFailed_Instance` | 복구 인스턴스 정상성 확인 |
| ALB Target Group | `HealthyHostCount`, `UnHealthyHostCount` | 트래픽 투입 가능 여부 확인 |

```bash
# 최근 백업 작업을 확인한다.
aws backup list-backup-jobs \
  --by-resource-type EC2 \
  --max-results 20 \
  --region ap-northeast-2 \
  --output json

# 최근 복구 작업을 확인한다.
aws backup list-restore-jobs \
  --by-resource-type EC2 \
  --max-results 20 \
  --region ap-northeast-2 \
  --output json
```

### 4.2 EventBridge로 AWS Backup 실패 알림

```json
{
  "source": ["aws.backup"],
  "detail-type": ["Backup Job State Change", "Restore Job State Change"],
  "detail": {
    "state": ["FAILED", "EXPIRED", "ABORTED"]
  }
}
```

실무에서는 위 EventBridge rule을 SNS, Slack Lambda, Incident Manager로 연결한다. 복구 작업 실패는 백업 실패보다 우선순위를 높게 둔다.

### 4.3 복구 인스턴스 CloudWatch 알람

```hcl
resource "aws_cloudwatch_metric_alarm" "restored_ec2_status_check_failed" {
  alarm_name          = "prod-restored-ec2-status-check-failed"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 2
  metric_name         = "StatusCheckFailed"
  namespace           = "AWS/EC2"
  period              = 60
  statistic           = "Maximum"
  threshold           = 1
  alarm_description   = "Restored EC2 instance status check failed"
  alarm_actions       = [aws_sns_topic.ops_alert.arn]

  dimensions = {
    InstanceId = "<RESTORED_INSTANCE_ID>"
  }

  tags = {
    Name        = "prod-restored-ec2-status-check-failed"
    Environment = "prod"
    Team        = "<TEAM_NAME>"
    ManagedBy   = "terraform"
  }
}
```

---

## 5. TIP

- AWS Backup EC2 복구는 기존 인스턴스를 되살리는 절차가 아니라 새 인스턴스를 만드는 절차임. instance ID, primary ENI, primary private IP 의존성을 제거해야 복구가 단순해짐.
- UserData는 백업·복구되지 않음. 복구 가능한 인프라는 Launch Template, Terraform, SSM, 구성 관리 도구에 선언해야 함.
- key pair는 복구 중 변경할 수 없음. SSH 키 분실 상황까지 고려하면 SSM Session Manager 접속 경로를 별도로 준비해야 함.
- 백업 성공 알림만으로는 부족함. 월 1회 이상 격리 Subnet에 실제 복구 테스트를 수행하고, ALB 등록 전까지 검증 Runbook을 자동화함.
- 교차 리전 복구를 운영 요구사항으로 잡았다면 KMS key, AMI 복사, Backup Vault 복사 규칙, Route 53 전환을 함께 테스트해야 함.
- 공식 문서:
  - [Restore an Amazon EC2 instance - AWS Backup](https://docs.aws.amazon.com/aws-backup/latest/devguide/restoring-ec2.html)
  - [StartRestoreJob - AWS Backup API Reference](https://docs.aws.amazon.com/aws-backup/latest/APIReference/API_StartRestoreJob.html)
  - [GetRecoveryPointRestoreMetadata - AWS Backup API Reference](https://docs.aws.amazon.com/aws-backup/latest/APIReference/API_GetRecoveryPointRestoreMetadata.html)
