# EBS 스냅샷으로 루트 볼륨 복구

## 1. 개요

- EBS 스냅샷(Snapshot)은 볼륨의 특정 시점 백업으로, 루트 볼륨 손상·부팅 불가·OS 오염 시 복구 수단으로 활용됨
- 루트 볼륨은 실행 중 교체가 불가능하므로, **인스턴스를 중지(Stop)한 뒤 볼륨 분리 → 스냅샷 기반 신규 볼륨 생성 → 재부착** 순으로 복구

---

## 2. 설명

### 2.1 핵심 개념

| 개념 | 설명 |
|------|------|
| 스냅샷 (Snapshot) | EBS 볼륨의 시점 기반 증분 백업, S3에 저장 |
| 루트 볼륨 (Root Volume) | `/dev/xvda` 또는 `/dev/nvme0n1`, OS가 부팅되는 볼륨 |
| AMI vs 스냅샷 | AMI는 스냅샷 + 부팅 정보(Block Device Mapping) 포함 / 스냅샷은 볼륨 데이터만 |
| Replace Root Volume | AWS 콘솔/CLI에서 지원하는 루트 볼륨 교체 기능 (인스턴스 중지 불필요, 단 재시작 필요) |

### 2.2 복구 방법 비교

| 방법 | 인스턴스 중지 필요 | 특징 |
|------|:-----------------:|------|
| 수동 교체 (Detach/Attach) | O | 모든 인스턴스 유형 지원, 정교한 제어 가능 |
| Replace Root Volume (콘솔/CLI) | X (재시작 필요) | Nitro 기반 인스턴스만 지원, 스냅샷 또는 AMI 지정 가능 |
| AMI로 새 인스턴스 생성 | - | 기존 인스턴스 IP·ENI 유지 불가 (ENI 재부착으로 우회 가능) |

---

### 2.3 방법 1 — 수동 Detach/Attach (범용)

#### 사전 준비: 스냅샷 생성

```bash
# 현재 루트 볼륨 ID 확인
INSTANCE_ID="i-0123456789abcdef0"

ROOT_VOLUME_ID=$(aws ec2 describe-instances \
  --instance-ids $INSTANCE_ID \
  --query 'Reservations[0].Instances[0].BlockDeviceMappings[?DeviceName==`/dev/xvda`].Ebs.VolumeId' \
  --output text)

echo "Root Volume: $ROOT_VOLUME_ID"

# 스냅샷 생성 (인스턴스 실행 중에도 가능, 단 파일시스템 freeze 권장)
SNAPSHOT_ID=$(aws ec2 create-snapshot \
  --volume-id $ROOT_VOLUME_ID \
  --description "root-volume-backup-$(date +%Y%m%d-%H%M%S)" \
  --tag-specifications 'ResourceType=snapshot,Tags=[{Key=Name,Value=root-backup},{Key=InstanceId,Value='"$INSTANCE_ID"'}]' \
  --query 'SnapshotId' \
  --output text)

echo "Snapshot: $SNAPSHOT_ID"

# 완료 대기
aws ec2 wait snapshot-completed --snapshot-ids $SNAPSHOT_ID
echo "Snapshot completed"
```

#### 복구 절차

```bash
AZ="ap-northeast-2a"   # 인스턴스와 동일한 가용 영역
VOLUME_TYPE="gp3"
VOLUME_SIZE=30         # GB (원본 이상으로 지정)

# Step 1: 인스턴스 중지
aws ec2 stop-instances --instance-ids $INSTANCE_ID
aws ec2 wait instance-stopped --instance-ids $INSTANCE_ID
echo "Instance stopped"

# Step 2: 스냅샷으로 신규 볼륨 생성
NEW_VOLUME_ID=$(aws ec2 create-volume \
  --snapshot-id $SNAPSHOT_ID \
  --availability-zone $AZ \
  --volume-type $VOLUME_TYPE \
  --size $VOLUME_SIZE \
  --tag-specifications 'ResourceType=volume,Tags=[{Key=Name,Value=root-restored}]' \
  --query 'VolumeId' \
  --output text)

aws ec2 wait volume-available --volume-ids $NEW_VOLUME_ID
echo "New volume ready: $NEW_VOLUME_ID"

# Step 3: 기존 루트 볼륨 분리
aws ec2 detach-volume --volume-id $ROOT_VOLUME_ID
aws ec2 wait volume-available --volume-ids $ROOT_VOLUME_ID
echo "Old volume detached"

# Step 4: 신규 볼륨 부착
aws ec2 attach-volume \
  --instance-id $INSTANCE_ID \
  --volume-id $NEW_VOLUME_ID \
  --device /dev/xvda

aws ec2 wait volume-in-use --volume-ids $NEW_VOLUME_ID
echo "New volume attached"

# Step 5: 인스턴스 시작
aws ec2 start-instances --instance-ids $INSTANCE_ID
aws ec2 wait instance-running --instance-ids $INSTANCE_ID
echo "Instance running"
```

---

### 2.4 방법 2 — Replace Root Volume (Nitro 전용, 간편)

```bash
# 스냅샷 기반으로 루트 볼륨 교체 (인스턴스 중지 불필요)
aws ec2 create-replace-root-volume-task \
  --instance-id $INSTANCE_ID \
  --snapshot-id $SNAPSHOT_ID \
  --delete-replaced-root-volume  # 교체된 구 볼륨 자동 삭제

# 작업 상태 확인
aws ec2 describe-replace-root-volume-tasks \
  --filters "Name=instance-id,Values=$INSTANCE_ID" \
  --query 'ReplaceRootVolumeTasks[0].{State:TaskState,SnapshotId:SnapshotId}'
```

> **주의**: 작업이 `succeeded` 상태가 되어도 인스턴스는 자동 재시작됨. 재시작 타이밍을 제어하려면 `--no-delete-replaced-root-volume`으로 수동 관리.

---

### 2.5 루트 볼륨 암호화 여부 처리

스냅샷이 암호화되지 않았을 때 암호화된 볼륨으로 복구하려면:

```bash
# 스냅샷 복사 시 암호화 적용
ENCRYPTED_SNAPSHOT_ID=$(aws ec2 copy-snapshot \
  --source-region ap-northeast-2 \
  --source-snapshot-id $SNAPSHOT_ID \
  --destination-region ap-northeast-2 \
  --encrypted \
  --kms-key-id alias/aws/ebs \
  --description "encrypted-copy" \
  --query 'SnapshotId' \
  --output text)

aws ec2 wait snapshot-completed --snapshot-ids $ENCRYPTED_SNAPSHOT_ID
```

---

### 2.6 보안/비용 Best Practice

| 항목 | 권장 설정 |
|------|-----------|
| 스냅샷 주기 | 중요 인스턴스 일 1회 이상, AWS Backup 또는 DLM으로 자동화 |
| 보존 기간 | 일별 7개, 주별 4개, 월별 12개 세대 관리 |
| 암호화 | EBS 기본 암호화 계정 설정 활성화 (`aws ec2 enable-ebs-encryption-by-default`) |
| 교차 리전 복사 | 재해 복구 대비, 주요 스냅샷은 다른 리전에도 복사 |
| 비용 | gp3 볼륨은 스냅샷 차등 저장(증분)이므로 초기 이후 비용 저렴, 불필요한 스냅샷은 즉시 삭제 |

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**증상: 신규 볼륨 부착 후 인스턴스가 부팅 불가 (`/dev/xvda` 디바이스명 불일치)**
- 원인: Nitro 기반 인스턴스는 실제 디바이스가 `/dev/nvme0n1`로 인식됨. `/etc/fstab`의 UUID가 스냅샷 시점과 다름
- 해결: 복구용 EC2에 볼륨을 데이터 디스크로 임시 부착 후 `/etc/fstab`을 UUID 기반으로 수정
  ```bash
  # 부착된 볼륨 마운트 후 fstab 확인
  lsblk -f
  # UUID 기반으로 수정
  sudo blkid /dev/nvme1n1p1
  sudo nano /mnt/recovery/etc/fstab
  ```

**증상: `detach-volume` 시 `VolumeInUse` 오류**
- 원인: 인스턴스가 완전히 중지되지 않은 상태
- 해결: `aws ec2 wait instance-stopped` 완료 후 재시도. 강제 분리는 데이터 손상 위험이 있으므로 지양

**증상: 복구 후 SSH 접속 가능하나 애플리케이션 오류**
- 원인: 스냅샷 시점과 현재 시점 간의 설정·데이터 불일치
- 해결: 스냅샷 생성 전 `sync && echo 3 > /proc/sys/vm/drop_caches`로 버퍼 플러시, 중요 서비스는 `systemctl stop` 후 스냅샷 권장

### 3.2 자주 발생하는 문제 (Q&A)

**Q: 스냅샷 생성 중 인스턴스를 사용해도 되나요?**
- A: 가능합니다. 스냅샷은 시작 시점의 데이터를 캡처하며 이후 변경사항은 증분으로 추적됩니다. 단, 데이터베이스 등 일관성이 중요한 워크로드는 flush/lock 후 생성을 권장합니다.

**Q: 루트 볼륨 크기를 늘려서 복구할 수 있나요?**
- A: `create-volume` 시 `--size`를 원본보다 크게 지정하면 됩니다. 부팅 후 파티션 확장이 필요합니다.
  ```bash
  # 파티션 및 파일시스템 확장 (AL2/AL2023)
  sudo growpart /dev/nvme0n1 1
  sudo xfs_growfs /          # XFS
  # sudo resize2fs /dev/nvme0n1p1  # ext4
  ```

**Q: Windows 인스턴스에서도 동일한 방법이 적용되나요?**
- A: 볼륨 교체 절차는 동일합니다. 단, 디바이스명은 `xvda` 대신 `/dev/sda1`(콘솔 표시)를 사용하고, 부팅 후 드라이버 재설치가 필요할 수 있습니다.

---

## 4. 모니터링 및 알람

```bash
# DLM(Data Lifecycle Manager)으로 자동 스냅샷 정책 생성
aws dlm create-lifecycle-policy \
  --description "daily-root-volume-backup" \
  --state ENABLED \
  --execution-role-arn arn:aws:iam::123456789012:role/AWSDataLifecycleManagerDefaultRole \
  --policy-details '{
    "PolicyType": "EBS_SNAPSHOT_MANAGEMENT",
    "ResourceTypes": ["INSTANCE"],
    "TargetTags": [{"Key": "Backup", "Value": "true"}],
    "Schedules": [{
      "Name": "daily",
      "CreateRule": {"Interval": 24, "IntervalUnit": "HOURS", "Times": ["03:00"]},
      "RetainRule": {"Count": 7},
      "CopyTags": true
    }]
  }'
```

**스냅샷 실패 감지 CloudWatch Events 룰**

```json
{
  "source": ["aws.ec2"],
  "detail-type": ["EBS Snapshot Notification"],
  "detail": {
    "event": ["createSnapshot"],
    "result": ["failed"]
  }
}
```

**주요 지표**

| 지표 | 확인 방법 |
|------|-----------|
| 스냅샷 완료 여부 | `aws ec2 describe-snapshots --snapshot-ids $SNAPSHOT_ID --query 'Snapshots[0].State'` |
| 볼륨 상태 | `aws ec2 describe-volume-status --volume-ids $VOLUME_ID` |
| Replace Root Volume 작업 상태 | `aws ec2 describe-replace-root-volume-tasks` |

---

## 5. TIP

- **스냅샷 전 태그 습관화**: `InstanceId`, `Environment`, `Date` 태그를 달아두면 대량 관리 시 필터링이 쉬움
- **AMI 생성 병행**: 스냅샷 단독보다 AMI로 만들어두면 새 인스턴스 생성·Launch Template 연동이 용이
- **복구 훈련(DR Drill)**: 분기 1회 이상 스냅샷 → 신규 볼륨 부착 → 부팅 확인 프로세스를 실제로 수행해 RTD(Recovery Time 목표) 검증
- **관련 문서**:
  - [EBS 스냅샷 공식 문서](https://docs.aws.amazon.com/ebs/latest/userguide/ebs-snapshots.html)
  - [루트 볼륨 교체 공식 문서](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/replace-root.html)
  - [DLM 공식 문서](https://docs.aws.amazon.com/ebs/latest/userguide/snapshot-lifecycle.html)
