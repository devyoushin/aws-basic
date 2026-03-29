# EBS 타입별 성능 & 튜닝

## 1. 개요

EBS (Elastic Block Store)는 EC2에 연결하는 네트워크 블록 스토리지다.
볼륨 타입 선택과 IOPS/처리량 설정이 애플리케이션 성능에 직접 영향을 미치며,
gp2 → gp3 마이그레이션만으로 비용 20% 절감이 가능하다.

---

## 2. 설명

### 2.1 핵심 개념

**EBS 볼륨 타입 비교표**

| 타입 | 분류 | 최대 IOPS | 최대 처리량 | 비용 (ap-northeast-2) | 용도 |
|------|------|-----------|------------|----------------------|------|
| gp3 | SSD 범용 | 16,000 | 1,000 MB/s | $0.08/GB | 대부분의 워크로드 (권장) |
| gp2 | SSD 범용 | 16,000 | 250 MB/s | $0.10/GB | 레거시 (gp3로 전환 권장) |
| io2 | SSD 프로비저닝 | 64,000 | 1,000 MB/s | $0.125/GB + IOPS | 고성능 DB (MSSQL, Oracle) |
| io1 | SSD 프로비저닝 | 64,000 | 1,000 MB/s | $0.125/GB + IOPS | io2 이전 세대 |
| st1 | HDD 처리량 최적화 | 500 | 500 MB/s | $0.045/GB | 로그, 데이터 웨어하우스 |
| sc1 | HDD 콜드 | 250 | 250 MB/s | $0.015/GB | 아카이브, 저빈도 접근 |

**gp2 vs gp3 핵심 차이**

| 항목 | gp2 | gp3 |
|------|-----|-----|
| 기본 IOPS | 3 IOPS/GB (최소 100) | 3,000 (고정) |
| 기본 처리량 | 128~250 MB/s | 125 MB/s |
| 최대 IOPS | 16,000 (5,334GB 이상) | 16,000 (독립 설정) |
| IOPS 설정 | 크기에 종속 | 크기와 독립적으로 설정 |
| 비용 | $0.10/GB | $0.08/GB |

→ **gp3는 IOPS와 처리량을 크기와 무관하게 독립적으로 설정 가능** — 작은 볼륨에서도 고성능 확보

**버스트 크레딧 (gp2)**
- 1TB 미만 gp2 볼륨은 버스트 크레딧으로 순간 3,000 IOPS 가능
- 크레딧 소진 시 베이스라인 IOPS로 성능 급락 (BurstBalance 지표 감시 필요)
- gp3는 버스트 개념 없이 항상 3,000 IOPS 기본 보장

**EBS 최적화 인스턴스 (EBS-Optimized)**
- 네트워크 트래픽과 EBS I/O 전용 대역폭 분리
- 현세대 인스턴스는 기본 활성화 (추가 비용 없음)
- EBS 성능이 기대 이하라면 인스턴스 수준 대역폭 한계 먼저 확인

---

### 2.2 실무 적용 코드

**Terraform — gp3 볼륨 생성 (IOPS/처리량 명시)**

```hcl
resource "aws_ebs_volume" "app_data" {
  availability_zone = "ap-northeast-2a"
  size              = 100   # GB
  type              = "gp3"
  iops              = 6000  # 기본 3,000에서 증설 (GB당 500 IOPS까지 무료)
  throughput        = 250   # MB/s (기본 125에서 증설, 최대 1,000)
  encrypted         = true
  kms_key_id        = aws_kms_key.ebs.arn

  tags = {
    Name = "app-data"
  }
}
```

**Terraform — EC2 루트 볼륨 gp3 설정**

```hcl
resource "aws_instance" "app" {
  ami           = data.aws_ami.al2023.id
  instance_type = "m5.xlarge"

  root_block_device {
    volume_type           = "gp3"
    volume_size           = 50
    iops                  = 3000
    throughput            = 125
    encrypted             = true
    delete_on_termination = true
  }

  ebs_block_device {
    device_name           = "/dev/sdb"
    volume_type           = "gp3"
    volume_size           = 200
    iops                  = 6000
    throughput            = 250
    encrypted             = true
    delete_on_termination = false
  }
}
```

**AWS CLI — gp2 → gp3 무중단 전환**

```bash
# 특정 볼륨을 gp3로 전환 (인스턴스 정지 불필요)
VOLUME_ID="vol-0123456789abcdef0"

aws ec2 modify-volume \
  --volume-id $VOLUME_ID \
  --volume-type gp3 \
  --iops 3000 \
  --throughput 125

# 변환 진행 상태 확인
aws ec2 describe-volumes-modifications \
  --volume-ids $VOLUME_ID \
  --query 'VolumesModifications[*].[VolumeId,ModificationState,Progress]' \
  --output table

# 계정 내 모든 gp2 볼륨 목록 (일괄 전환 전 확인)
aws ec2 describe-volumes \
  --filters "Name=volume-type,Values=gp2" \
  --query 'Volumes[*].[VolumeId,Size,State,Attachments[0].InstanceId]' \
  --output table
```

**fio — EBS 성능 벤치마크**

```bash
# 랜덤 읽기 IOPS 측정 (4KB 블록)
sudo fio \
  --name=rand-read-iops \
  --ioengine=libaio \
  --rw=randread \
  --bs=4k \
  --direct=1 \
  --size=1G \
  --numjobs=4 \
  --iodepth=64 \
  --runtime=60 \
  --group_reporting \
  --filename=/dev/nvme1n1

# 순차 쓰기 처리량 측정 (128KB 블록)
sudo fio \
  --name=seq-write-throughput \
  --ioengine=libaio \
  --rw=write \
  --bs=128k \
  --direct=1 \
  --size=2G \
  --numjobs=1 \
  --iodepth=32 \
  --runtime=60 \
  --group_reporting \
  --filename=/dev/nvme1n1
```

---

### 2.3 보안/비용 Best Practice

- **모든 EBS 볼륨 암호화 필수**: AWS Organizations SCP로 암호화되지 않은 볼륨 생성 차단
- **gp2 볼륨 일괄 gp3 전환**: 대부분의 경우 성능 동일하거나 향상, 비용 20% 절감
- **IOPS 과다 프로비저닝 금지**: io2 볼륨 IOPS는 실제 사용량 p99 기준으로 설정 (Compute Optimizer 활용)
- **미사용 볼륨 정리**: 인스턴스 삭제 후 남은 `available` 상태 볼륨 정기 정리
- **스냅샷 정책**: Data Lifecycle Manager(DLM)로 자동 스냅샷 + 보관 주기 설정

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**gp2 BurstBalance 고갈로 인한 성능 급락**

증상: 평소에는 빠르다가 특정 시간대에 I/O 성능이 크게 저하됨
원인: gp2 버스트 크레딧 소진 (BurstBalance = 0)
해결:
```bash
# BurstBalance 현재 값 확인
aws cloudwatch get-metric-statistics \
  --namespace AWS/EBS \
  --metric-name BurstBalance \
  --dimensions Name=VolumeId,Value=vol-xxxxxxxx \
  --start-time $(date -u -v-1H +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300 \
  --statistics Average

# 해결: gp3로 전환 (버스트 개념 없음, 항상 3,000 IOPS 보장)
aws ec2 modify-volume --volume-id vol-xxxxxxxx --volume-type gp3
```

**볼륨 크기 축소 불가**

증상: EBS 볼륨 크기를 줄이려고 하면 오류 발생
원인: EBS는 크기 축소를 지원하지 않음
해결:
1. 새로운 작은 볼륨 생성
2. rsync 또는 dd로 데이터 복사
3. 파일시스템 resize2fs / xfs_growfs (새 볼륨 기준으로 재포맷 후 복원)

**스냅샷 생성 중 I/O 성능 저하**

증상: 스냅샷 생성 시 지연 시간 증가
원인: 첫 스냅샷 생성 시 전체 데이터 전송, 이후에는 증분
해결: 트래픽 적은 시간대에 스냅샷 스케줄링 (DLM 활용)

### 3.2 자주 발생하는 문제 (Q&A)

**Q: gp3로 전환했는데 처리량이 오히려 줄었습니다**
A: gp3 기본 처리량은 125 MB/s이고, gp2는 크기에 따라 최대 250 MB/s까지 자동 제공됩니다. gp3 전환 시 throughput 파라미터를 명시적으로 설정하세요 (최대 1,000 MB/s).

**Q: IOPS가 프로비저닝한 값에 도달하지 않습니다**
A: 인스턴스 레벨 EBS 대역폭 한계를 확인하세요. 예를 들어 m5.xlarge는 최대 4,750 Mbps EBS 대역폭을 가집니다. 인스턴스 한계가 볼륨 한계보다 낮을 수 있습니다.

---

## 4. 모니터링 및 알람

```hcl
# gp2 BurstBalance 소진 알람
resource "aws_cloudwatch_metric_alarm" "ebs_burst_balance" {
  alarm_name          = "ebs-burst-balance-low"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  metric_name         = "BurstBalance"
  namespace           = "AWS/EBS"
  period              = 300
  statistic           = "Average"
  threshold           = 20  # 20% 미만 시 알람

  dimensions = {
    VolumeId = "vol-xxxxxxxx"
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
}

# VolumeQueueLength 높음 (I/O 병목)
resource "aws_cloudwatch_metric_alarm" "ebs_queue_length" {
  alarm_name          = "ebs-queue-length-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "VolumeQueueLength"
  namespace           = "AWS/EBS"
  period              = 60
  statistic           = "Average"
  threshold           = 1   # 큐 1 이상이면 I/O 대기 발생 중
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
```

**핵심 지표**

| 지표 | 의미 | 임계값 기준 |
|------|------|------------|
| `BurstBalance` | 버스트 크레딧 잔량 (gp2) | 20% 미만 시 위험 |
| `VolumeQueueLength` | I/O 대기 중인 요청 수 | 1 이상이면 병목 |
| `VolumeReadOps/WriteOps` | 초당 I/O 요청 수 | 프로비저닝 IOPS 95% 초과 시 알람 |
| `VolumeThroughputPercentage` | 처리량 사용률 | 80% 초과 시 증설 검토 |

---

## 5. TIP

- **Nitro 기반 인스턴스에서 NVMe 디바이스 이름**: `/dev/nvme0n1` (루트), `/dev/nvme1n1` (추가 볼륨) — `/dev/xvdf` 등과 혼동 주의
- **볼륨 크기 변경 후 파일시스템 확장 필수**: 볼륨 크기를 늘려도 OS 레벨 파일시스템은 수동 확장 필요
  ```bash
  # 파티션 확장 (필요 시)
  sudo growpart /dev/nvme0n1 1
  # ext4
  sudo resize2fs /dev/nvme0n1p1
  # xfs
  sudo xfs_growfs /
  ```
- Compute Optimizer를 통해 과다 프로비저닝된 io1/io2 볼륨의 gp3 전환 권고 확인 가능
