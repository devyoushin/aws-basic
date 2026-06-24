# AMI 백업으로 EC2 인스턴스 복구

## 1. 개요

AMI(Amazon Machine Image)는 하나 이상의 EBS 스냅샷, block device mapping, 부팅 정보, launch permission으로 구성된 인스턴스 템플릿이다. AMI 복구는 손상된 인스턴스를 되살리는 작업이 아니라, AMI에서 **새 EC2 인스턴스**를 기동한 뒤 네트워크·데이터·트래픽을 새 인스턴스로 전환하는 작업이다.

루트 볼륨만 손상됐고 instance ID·primary private IP를 유지해야 하면 루트 볼륨 교체가 맞다. OS·애플리케이션 전체를 알려진 정상 시점으로 되돌리거나 기존 인스턴스가 멈춘 상태라면 AMI 기반 새 인스턴스 복구가 적합하다.

---

## 2. 설명

### 2.1 AMI 백업 범위와 복구 대상

| 항목 | AMI에 포함 여부 | 복구 시 처리 |
|---|---|---|
| 루트 EBS 볼륨 | 포함 | AMI snapshot에서 새 루트 볼륨 생성 |
| 생성 시 포함한 추가 EBS 볼륨 | 포함 가능 | block device mapping에 있으면 새 인스턴스에 함께 생성 |
| 인스턴스 스토어 데이터 | 미포함 | 별도 EBS·S3 백업에서 복구 |
| instance ID, 실행 상태 | 미포함 | 새 instance ID로 기동 |
| primary ENI, primary private IP | 미포함 | 새 primary ENI와 private IP가 할당됨 |
| Elastic IP | AMI에 미포함 | 새 인스턴스 또는 새 primary ENI에 재연결 |
| IAM instance profile, security group, UserData | AMI에 미포함 | Launch Template 또는 복구 명령에서 명시 |
| 애플리케이션의 외부 데이터(RDS, EFS, S3 등) | 미포함 | 별도 백업·복구 정책 적용 |

AMI는 EBS 볼륨의 백업이지 AWS 리소스 전체의 백업이 아니다. 특히 UserData는 AMI 생성·AWS Backup EC2 복구 범위에 포함되지 않으므로, 초기화에 필요한 설정은 Launch Template, SSM Parameter Store, IaC에서 재현 가능해야 한다.

### 2.2 복구 방식 선택

| 상황 | 권장 방식 | 이유 |
|---|---|---|
| OS 설정·애플리케이션을 정상 시점으로 전면 복구 | AMI에서 새 인스턴스 기동 | 빠르게 알려진 정상 이미지로 대체 가능 |
| instance ID·primary private IP 유지가 필수 | 루트 EBS 교체 | AMI 새 기동은 새 primary ENI를 사용 |
| ASG/ALB 뒤의 stateless 애플리케이션 | Launch Template의 승인 AMI로 ASG 교체 | 기존 인스턴스 수리보다 빠르고 일관적 |
| 데이터베이스·상태 데이터가 로컬 EBS에 있음 | 데이터 볼륨 스냅샷 정합성 확인 후 별도 복구 | AMI 시점과 데이터 시점 불일치 방지 |
| 다른 리전 장애 복구 | 대상 리전에 복사한 AMI로 기동 | AMI와 암호화 KMS key의 리전 제약 처리 필요 |

### 2.3 사전 준비: AMI 생성과 완료 확인

기본값으로 AMI 생성 시 EC2는 인스턴스를 재부팅해 파일시스템의 정합성을 높인다. `--no-reboot`는 중단을 피할 수 있으나 파일시스템 정합성을 보장하지 않으므로, 운영 백업에는 애플리케이션 quiesce 또는 일시 중단 후 기본 동작을 사용한다.

```bash
# EBS-backed 인스턴스에서 복구용 AMI를 생성한다. 기본값은 재부팅을 수행한다.
aws ec2 create-image \
  --instance-id <SOURCE_INSTANCE_ID> \
  --name "prod-app-recovery-<YYYYMMDDHHMM>" \
  --description "Known-good recovery image" \
  --tag-specifications 'ResourceType=image,Tags=[{Key=Name,Value=prod-app-recovery},{Key=Purpose,Value=disaster-recovery}]' \
  --region ap-northeast-2 \
  --output json

# AMI가 available 상태가 될 때까지 기다린다. 출력된 AMI ID를 입력한다.
aws ec2 wait image-available \
  --image-ids <AMI_ID> \
  --region ap-northeast-2

# AMI의 루트·데이터 EBS snapshot과 device mapping을 확인한다.
aws ec2 describe-images \
  --image-ids <AMI_ID> \
  --query 'Images[0].{State:State,RootDeviceName:RootDeviceName,BlockDeviceMappings:BlockDeviceMappings}' \
  --region ap-northeast-2 \
  --output json
```

> **주의**: `--no-reboot`로 생성한 AMI는 장애 중인 인스턴스에서 최후 수단으로 사용한다. 데이터베이스 등 쓰기 일관성이 필요한 서비스는 애플리케이션 자체 백업·로그 복구 절차가 AMI보다 우선이다.

### 2.4 장애 시 복구 절차

#### 1) 원본 인스턴스의 복구 입력값 확보

원본을 종료하거나 EIP를 옮기기 전에, 새 인스턴스에 필요한 subnet, security group, IAM profile, key pair, EBS 매핑을 기록한다. 원본이 실행 중이면 데이터 쓰기를 중지하거나 Target Group에서 먼저 제거한다.

```bash
# 원본의 네트워크·IAM profile·키 페어·보안 그룹·EBS 매핑을 수집한다.
aws ec2 describe-instances \
  --instance-ids <SOURCE_INSTANCE_ID> \
  --query 'Reservations[0].Instances[0].{SubnetId:SubnetId,AvailabilityZone:Placement.AvailabilityZone,SecurityGroups:SecurityGroups,IamInstanceProfile:IamInstanceProfile,KeyName:KeyName,BlockDeviceMappings:BlockDeviceMappings}' \
  --region ap-northeast-2 \
  --output json

# EIP가 연결되어 있다면 재연결에 필요한 Allocation ID를 확인한다.
aws ec2 describe-addresses \
  --filters Name=instance-id,Values=<SOURCE_INSTANCE_ID> \
  --query 'Addresses[*].{AllocationId:AllocationId,PublicIp:PublicIp,AssociationId:AssociationId}' \
  --region ap-northeast-2 \
  --output json

# 원본을 ALB Target Group에서 제외하고 connection draining 완료를 기다린다.
aws elbv2 deregister-targets \
  --target-group-arn <TARGET_GROUP_ARN> \
  --targets Id=<SOURCE_INSTANCE_ID> \
  --region ap-northeast-2 \
  --output json
```

#### 2) AMI에서 새 인스턴스 기동

동일 AZ에서 별도 데이터 EBS를 다시 Attach해야 하는 경우에는 원본과 같은 AZ를 선택한다. AMI에 포함된 EBS는 새 볼륨으로 생성되므로, 기존 볼륨을 그대로 보존해야 한다면 해당 볼륨을 AMI block device mapping에서 제외하고 별도로 Attach한다.

```bash
# AMI에서 새 인스턴스를 기동한다. subnet, security group, IAM profile을 명시한다.
aws ec2 run-instances \
  --image-id <AMI_ID> \
  --instance-type <INSTANCE_TYPE> \
  --subnet-id <SUBNET_ID> \
  --security-group-ids <SECURITY_GROUP_ID> \
  --iam-instance-profile Name=<IAM_INSTANCE_PROFILE_NAME> \
  --key-name <KEY_PAIR_NAME> \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=prod-app-recovered},{Key=RecoverySource,Value=<AMI_ID>}]]' \
  --region ap-northeast-2 \
  --output json

# 새 인스턴스가 running 상태가 될 때까지 기다린다.
aws ec2 wait instance-running \
  --instance-ids <RECOVERY_INSTANCE_ID> \
  --region ap-northeast-2

# System과 Instance 상태 체크가 모두 통과했는지 확인한다.
aws ec2 describe-instance-status \
  --instance-ids <RECOVERY_INSTANCE_ID> \
  --region ap-northeast-2 \
  --output json
```

복구 절차를 반복해야 하는 환경은 `run-instances` 대신 기존 Launch Template의 알려진 정상 버전을 사용한다. Launch Template에는 AMI, instance type, IAM profile, security group, block device mapping, UserData를 함께 선언해 누락을 줄인다.

#### 3) 데이터·네트워크·트래픽 전환

새 인스턴스는 원본 primary ENI를 가져올 수 없다. primary ENI는 분리할 수 없으므로 기존 private IP가 하드코딩된 구조라면 DNS 또는 로드밸런서를 전환점으로 사용해야 한다. Elastic IP는 새 인스턴스에 재연결할 수 있다.

```bash
# EIP를 새 인스턴스에 연결한다. 기존 연결은 자동으로 교체된다.
aws ec2 associate-address \
  --allocation-id <EIP_ALLOCATION_ID> \
  --instance-id <RECOVERY_INSTANCE_ID> \
  --allow-reassociation \
  --region ap-northeast-2 \
  --output json

# 기존 데이터 EBS를 사용하는 경우에만, 같은 AZ의 새 인스턴스에 Attach한다.
aws ec2 attach-volume \
  --volume-id <DATA_VOLUME_ID> \
  --instance-id <RECOVERY_INSTANCE_ID> \
  --device /dev/sdf \
  --region ap-northeast-2 \
  --output json

# 애플리케이션 검증을 마친 새 인스턴스를 Target Group에 등록한다.
aws elbv2 register-targets \
  --target-group-arn <TARGET_GROUP_ARN> \
  --targets Id=<RECOVERY_INSTANCE_ID>,Port=<APPLICATION_PORT> \
  --region ap-northeast-2 \
  --output json

# Target Group health check가 healthy가 될 때까지 기다린다.
aws elbv2 wait target-in-service \
  --target-group-arn <TARGET_GROUP_ARN> \
  --targets Id=<RECOVERY_INSTANCE_ID>,Port=<APPLICATION_PORT> \
  --region ap-northeast-2
```

데이터 EBS를 원본에서 새 인스턴스로 옮길 때는 원본 인스턴스 중지 또는 해당 파일시스템 unmount가 선행돼야 한다. 두 인스턴스에 같은 일반 EBS 볼륨을 동시에 Attach하지 않는다.

#### 4) 검증 후 원본 처리

```bash
# 새 인스턴스의 애플리케이션 상태를 SSM으로 확인한다.
aws ssm send-command \
  --instance-ids <RECOVERY_INSTANCE_ID> \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["systemctl --failed","df -h","curl -fsS http://localhost:<APPLICATION_PORT>/health"]' \
  --region ap-northeast-2 \
  --output json

# 정상 Target 상태와 연결 정보를 최종 확인한다.
aws elbv2 describe-target-health \
  --target-group-arn <TARGET_GROUP_ARN> \
  --targets Id=<RECOVERY_INSTANCE_ID>,Port=<APPLICATION_PORT> \
  --region ap-northeast-2 \
  --output json
```

새 인스턴스의 상태 체크, 애플리케이션 health check, 로그, 데이터 정합성, 외부 모니터링이 모두 정상인 것을 확인한 뒤에만 원본을 종료한다. 원인 분석이 필요하면 원본 EBS와 인스턴스는 일정 기간 보존한다.

### 2.5 AMI 기반 복구 자동화

ASG 환경은 인스턴스를 수동으로 AMI에서 기동하지 않는다. 승인된 AMI ID를 포함한 Launch Template 새 버전을 만들고 ASG Instance Refresh 또는 인스턴스 교체를 사용한다. 이를 통해 Target Group 등록, desired capacity 유지, 새 인스턴스의 정상화가 ASG 흐름에 포함된다.

```hcl
# 복구에 검증된 AMI를 Launch Template에 고정한다.
resource "aws_launch_template" "app_recovery" {
  name_prefix   = "prod-app-recovery-"
  image_id      = var.recovery_ami_id
  instance_type = "t3.medium"

  iam_instance_profile {
    name = aws_iam_instance_profile.app.name
  }

  vpc_security_group_ids = [aws_security_group.app.id]

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name           = "prod-app"
      RecoverySource = var.recovery_ami_id
      ManagedBy      = "terraform"
    }
  }
}
```

---

## 3. 트러블슈팅

### 3.1 새 인스턴스가 기존 private IP로 접속되지 않음

#### 원인

AMI에서 새로 기동한 인스턴스는 새 primary ENI를 받는다. 기존 인스턴스의 primary ENI는 다른 인스턴스로 이동할 수 없다.

#### 해결 방법

ALB/NLB Target Group 또는 Route 53 레코드를 새 인스턴스로 전환한다. 고정 public IPv4가 필요하면 EIP를 새 인스턴스에 재연결한다. 서비스 의존성에 private IP가 직접 하드코딩되어 있다면 DNS 기반 이름 해석으로 전환한다.

### 3.2 AMI에서 새 인스턴스를 기동했지만 데이터가 최신이 아님

#### 원인

AMI는 생성 시점의 EBS snapshot을 사용한다. AMI 생성 이후 데이터베이스·파일 쓰기는 복구 이미지에 반영되지 않는다.

#### 해결 방법

데이터베이스는 native backup, PITR, replica failover를 사용하고, 파일 데이터는 별도 EBS snapshot·EFS·S3 백업을 복구한다. 애플리케이션 바이너리용 AMI와 상태 데이터용 백업의 RPO를 분리해 설계한다.

### 3.3 새 인스턴스가 Target Group에서 unhealthy

#### 원인

AMI에 포함되지 않은 security group, IAM profile, UserData 환경 변수, 외부 설정 또는 데이터 EBS mount가 누락된 경우다.

#### 해결 방법

ALB health check 경로·포트, ALB→EC2 security group 규칙, `systemctl --failed`, `/etc/fstab`, SSM Parameter Store 접근 권한을 확인한다. 반복되는 누락은 Launch Template·Terraform에 선언해 복구 절차에서 제거한다.

---

## 4. 모니터링 및 알람

AMI 기반 복구의 완료 기준은 EC2 상태 체크만이 아니다. 인프라, Target Group, 애플리케이션을 함께 감시한다.

| 확인 항목 | 정상 기준 | 실패 시 조치 |
|---|---|---|
| EC2 `StatusCheckFailed` | 0 | 부팅 로그·상태 체크 원인 분석 |
| ALB `HealthyHostCount` | 최소 운영 대수 이상 | 새 인스턴스 등록·health check 설정 확인 |
| ALB `HTTPCode_Target_5XX_Count` | 평시 범위 | 데이터·환경 설정·애플리케이션 로그 확인 |
| Synthetics/외부 health check | 성공 | EIP·DNS·WAF·TLS 및 외부 연결 점검 |

```hcl
# 복구 인스턴스가 Target Group healthy가 되지 않는 상황을 감지한다.
resource "aws_cloudwatch_metric_alarm" "recovery_target_unhealthy" {
  alarm_name          = "prod-app-recovery-no-healthy-target"
  alarm_description   = "AMI 복구 후 ALB Target Group에 healthy 인스턴스가 없음"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HealthyHostCount"
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"

  dimensions = {
    LoadBalancer = aws_lb.app.arn_suffix
    TargetGroup  = aws_lb_target_group.app.arn_suffix
  }

  alarm_actions = [aws_sns_topic.ops_alert.arn]
}
```

---

## 5. TIP

- AMI는 정기적으로 생성하는 것만으로 충분하지 않다. 분기마다 별도 subnet 또는 staging에서 AMI 기동, SSM 연결, 애플리케이션 health check, Target Group 등록까지 복구 훈련을 수행한다.
- AMI와 연결된 EBS snapshot을 무심코 삭제하면 해당 AMI로 인스턴스를 기동할 수 없다. AMI 정리 시에는 deregister 이후 어떤 snapshot이 다른 AMI에서도 사용되는지 확인한다.
- 암호화된 AMI를 다른 리전·계정에서 사용하려면 snapshot과 KMS key 정책을 함께 공유하거나 복사한다.
- 루트 볼륨만 복구하는 절차는 [EBS 스냅샷으로 루트 볼륨 복구](ec2-snapshot-root-volume-recovery.md), 상태 체크 실패의 원인별 대응은 [EC2 상태 체크 실패 대응](ec2-status-check-failure.md)을 참고한다.
- 참고: [AMI 생성](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/creating-an-ami-ebs.html), [EC2 backup and recovery with AMIs](https://docs.aws.amazon.com/prescriptive-guidance/latest/backup-recovery/ec2-backup.html), [ENI attachment 제약](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/network-interface-attachments.html)
