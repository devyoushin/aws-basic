# EC2 EBS를 LVM으로 분할하고 영구 마운트하기

## 1. 개요

새 EBS(Elastic Block Store) 볼륨 하나를 EC2에 연결하고, LVM(Logical Volume Manager)으로 논리 볼륨(Logical Volume) 3개를 만든 뒤 파일시스템, 마운트, `/etc/fstab` 영구 등록까지 수행하는 절차.

예시 목표:

| 구분 | 설정값 |
|---|---|
| EBS 볼륨 | gp3, 암호화, 303 GiB |
| 볼륨 그룹 (Volume Group) | `vg_data` |
| 논리 볼륨 | `lv_app`, `lv_log`, `lv_backup` 각각 100 GiB |
| 파일시스템 | XFS |
| 마운트 경로 | `/data/app`, `/data/log`, `/data/backup` |

EBS 크기를 300 GiB로 생성하고 LVM에서 `100G` 논리 볼륨을 3개 만들면 실패할 수 있음. 물리 볼륨(Physical Volume) 메타데이터와 확장 영역(Physical Extent) 정렬 때문에 실제 LVM 사용 가능 크기가 300 GiB보다 작기 때문임. **각 논리 볼륨에 정확히 100 GiB가 필요하면 EBS를 303 GiB 이상으로 생성**함.

> 아래 명령은 새 볼륨의 기존 데이터를 모두 삭제함. 루트 볼륨 또는 사용 중인 디스크에 실행 금지.

---

## 2. 설명

### 2.1 작업 전 확인

| 확인 항목 | 확인 방법 | 기준 |
|---|---|---|
| 가용 영역 (Availability Zone) | EC2 인스턴스와 EBS 볼륨의 AZ 확인 | 반드시 동일한 AZ |
| 대상 EBS | EC2 콘솔의 Volume ID, 태그 확인 | 새로 만든 대상 볼륨만 선택 |
| 디바이스 이름 | `lsblk -f`, `findmnt /` | 루트 디스크와 기존 마운트 디스크 제외 |
| 패키지 | `lvm2`, `xfsprogs` 설치 여부 | LVM 및 XFS 명령 사용 가능 |
| 복구 수단 | 볼륨 생성 직후 스냅샷 또는 변경 전 백업 | 포맷 전 복구 지점 확보 |

Linux의 `G` 단위는 GiB 기준으로 동작함. 따라서 `lvcreate -L 100G`는 100 GiB 논리 볼륨을 요청함.

### 2.2 EBS 생성 및 EC2 연결

콘솔에서 다음 값으로 EBS를 생성하고 대상 EC2에 연결함.

| 항목 | 예시 값 | 이유 |
|---|---|---|
| 크기 | `303 GiB` | 100 GiB 논리 볼륨 3개와 LVM 메타데이터 여유 확보 |
| 유형 | `gp3` | 대부분의 범용 서버 데이터 디스크에 사용 |
| 가용 영역 | 인스턴스와 동일한 AZ | EBS 연결 필수 조건 |
| 암호화 | 활성화 | 저장 데이터 보호 |
| 연결 시 디바이스 이름 | `/dev/sdf` | EC2 API 상 요청 이름; Nitro에서는 실제 이름이 달라질 수 있음 |
| 종료 시 삭제 | 운영 데이터 볼륨은 비활성 검토 | 인스턴스 종료 시 데이터 보존 |

AWS CLI 예시:

```bash
INSTANCE_ID="i-0123456789abcdef0"
AVAILABILITY_ZONE="ap-northeast-2a"

VOLUME_ID=$(aws ec2 create-volume \
  --availability-zone "${AVAILABILITY_ZONE}" \
  --size 303 \
  --volume-type gp3 \
  --encrypted \
  --tag-specifications 'ResourceType=volume,Tags=[{Key=Name,Value=app-lvm-data}]' \
  --query 'VolumeId' \
  --output text)

aws ec2 wait volume-available --volume-ids "${VOLUME_ID}"

aws ec2 attach-volume \
  --volume-id "${VOLUME_ID}" \
  --instance-id "${INSTANCE_ID}" \
  --device /dev/sdf
```

Nitro 기반 EC2에서는 `/dev/sdf`로 연결을 요청해도 운영체제에 `/dev/nvme1n1`처럼 표시됨. 순번은 연결 순서에 따라 달라지므로 `/dev/nvme1n1`을 고정값으로 가정하지 않음.

### 2.3 대상 디스크 식별

Amazon Linux 2023 예시. RHEL 계열도 동일한 절차를 사용함.

```bash
sudo dnf install -y lvm2 xfsprogs nvme-cli

# 디스크, 파일시스템, 마운트 상태 확인
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS,SERIAL
findmnt /

# Nitro 인스턴스에서 EBS Volume ID와 NVMe 디스크를 연결해 확인
sudo nvme list
sudo nvme id-ctrl -v /dev/nvme1n1 | grep -i '^sn'
```

`SERIAL` 또는 NVMe serial number에 EBS Volume ID가 표시됨. 예를 들어 `vol0123456789abcdef0`는 AWS의 `vol-0123456789abcdef0`와 대응함. 아래에서는 확인된 새 디스크를 `DEVICE`에 대입함.

```bash
DEVICE="/dev/nvme1n1"

# 대상이 빈 새 디스크인지 마지막으로 확인
lsblk -f "${DEVICE}"
sudo wipefs -n "${DEVICE}"
```

`wipefs -n` 출력에 기존 파일시스템 또는 파티션 서명이 있으면 중지하고 대상 Volume ID를 다시 확인함.

### 2.4 LVM 생성과 100 GiB 논리 볼륨 3개 분할

파티션 없이 EBS 전체를 LVM 물리 볼륨으로 사용함. 단일 EBS를 향후 확장할 계획이 있으면 같은 볼륨 그룹에 새 EBS를 추가할 수 있음.

```bash
DEVICE="/dev/nvme1n1"
VG_NAME="vg_data"

sudo pvcreate "${DEVICE}"
sudo vgcreate "${VG_NAME}" "${DEVICE}"

sudo lvcreate -L 100G -n lv_app "${VG_NAME}"
sudo lvcreate -L 100G -n lv_log "${VG_NAME}"
sudo lvcreate -L 100G -n lv_backup "${VG_NAME}"

# 3개의 LV와 VG 여유 공간 확인
sudo pvs
sudo vgs
sudo lvs -o vg_name,lv_name,lv_size,lv_path
```

300 GiB EBS만 확보된 상태라면 다음 중 하나를 선택함.

| 요구 사항 | 처리 방법 |
|---|---|
| 각 볼륨이 정확히 100 GiB 필요 | EBS를 303 GiB 이상으로 생성 |
| 300 GiB를 유지해야 함 | `lvcreate -L 99G`로 3개 생성하고 남은 공간을 VG 여유 공간으로 유지 |
| 용량 비율만 1:1:1이면 됨 | `lvcreate -l 33%VG`를 세 번 실행; 각 LV 크기는 100 GiB보다 작음 |

### 2.5 파일시스템 생성 및 마운트

XFS는 로그나 대용량 데이터 경로에 일반적으로 사용함. XFS는 축소를 지원하지 않으므로, 용량 축소 요구가 있는 경로는 ext4 사용 여부를 설계 단계에서 결정함.

```bash
sudo mkfs.xfs -f /dev/vg_data/lv_app
sudo mkfs.xfs -f /dev/vg_data/lv_log
sudo mkfs.xfs -f /dev/vg_data/lv_backup

sudo mkdir -p /data/app /data/log /data/backup

sudo mount /dev/vg_data/lv_app /data/app
sudo mount /dev/vg_data/lv_log /data/log
sudo mount /dev/vg_data/lv_backup /data/backup

df -hT /data/app /data/log /data/backup
```

애플리케이션 실행 계정이 별도로 있으면 마운트 후 소유권과 권한을 설정함.

```bash
APP_USER="<APP_USER>"
APP_GROUP="<APP_GROUP>"

sudo chown "${APP_USER}:${APP_GROUP}" /data/app /data/log /data/backup
sudo chmod 0750 /data/app /data/log /data/backup
```

### 2.6 UUID 기반 `/etc/fstab` 영구 등록

`/dev/nvme1n1`, `/dev/sdf` 같은 디바이스 이름은 재부팅이나 연결 순서에 따라 달라질 수 있음. 파일시스템 UUID로 `/etc/fstab`을 등록함.

```bash
sudo blkid /dev/vg_data/lv_app /dev/vg_data/lv_log /dev/vg_data/lv_backup
```

출력된 UUID를 사용해 `/etc/fstab`에 다음 행을 추가함. `<UUID_...>` 값은 실제 값으로 교체함.

```fstab
UUID=<UUID_LV_APP>    /data/app     xfs  defaults,nofail,x-systemd.device-timeout=30s  0  2
UUID=<UUID_LV_LOG>    /data/log     xfs  defaults,nofail,x-systemd.device-timeout=30s  0  2
UUID=<UUID_LV_BACKUP> /data/backup  xfs  defaults,nofail,x-systemd.device-timeout=30s  0  2
```

안전하게 적용하는 순서:

```bash
sudo cp -a /etc/fstab "/etc/fstab.$(date +%Y%m%d%H%M%S).bak"
sudoedit /etc/fstab

# 문법과 모든 등록 경로 검증. 오류가 있으면 재부팅하지 않음.
sudo findmnt --verify --verbose
sudo mount -a
findmnt /data/app /data/log /data/backup
```

`nofail`은 EBS 연결 지연 또는 누락 시 OS 부팅이 복구 모드로 빠지는 것을 줄임. 데이터 디스크 없이 애플리케이션이 시작되면 안 되는 서비스는 systemd unit에 `RequiresMountsFor=/data/app /data/log`를 추가해 서비스 시작 조건을 강제함.

```ini
# /etc/systemd/system/<APP_SERVICE>.service.d/mounts.conf
[Unit]
RequiresMountsFor=/data/app /data/log
```

```bash
sudo systemctl daemon-reload
sudo systemctl restart <APP_SERVICE>
```

### 2.7 완료 검증

```bash
sudo pvs
sudo vgs
sudo lvs -o vg_name,lv_name,lv_size,lv_path
lsblk -f
findmnt /data/app /data/log /data/backup
df -hT /data/app /data/log /data/backup

# 재부팅 전 fstab 재검증
sudo findmnt --verify --verbose
```

검증 항목:

| 항목 | 기대 결과 |
|---|---|
| `vgs` | `vg_data`가 표시되고 예상한 여유 공간만 남음 |
| `lvs` | 100 GiB `lv_app`, `lv_log`, `lv_backup` 표시 |
| `findmnt` | 각 `/data/*` 경로가 해당 `/dev/mapper/vg_data-*`에 연결 |
| `findmnt --verify` | `/etc/fstab` 오류 없음 |
| 애플리케이션 | 마운트 경로에 쓰기/읽기 및 재기동 정상 |

---

## 3. 트러블슈팅

### 3.1 `lvcreate -L 100G` 세 번째 생성이 실패함

#### 증상

```text
Insufficient free space: ... extents needed, but only ... available
```

#### 원인

300 GiB EBS의 LVM 물리 볼륨은 메타데이터 영역을 제외하면 300 GiB보다 작음. 100 GiB 논리 볼륨 3개를 정확히 만들 공간이 없음.

#### 해결 방법

새 EBS라면 303 GiB 이상으로 다시 생성함. 이미 사용 중인 EBS라면 `aws ec2 modify-volume`으로 크기를 늘린 후 물리 볼륨을 확장함.

```bash
VOLUME_ID="vol-0123456789abcdef0"

aws ec2 modify-volume \
  --volume-id "${VOLUME_ID}" \
  --size 303

aws ec2 wait volume-modified --volume-ids "${VOLUME_ID}"

# OS가 변경된 디스크 크기를 인식한 뒤 실행
sudo pvresize /dev/nvme1n1
sudo vgs
```

`modify-volume`의 완료 상태와 OS의 디스크 인식 상태를 모두 확인한 뒤 `pvresize`를 실행함.

### 3.2 `/dev/sdf`가 없고 `/dev/nvme1n1`만 보임

#### 증상

EC2 연결 요청은 `/dev/sdf`로 했지만 운영체제에서 해당 경로가 보이지 않음.

#### 원인

Nitro 기반 EC2는 EBS를 NVMe 디바이스로 노출함. EC2 API 디바이스 이름과 Linux 디바이스 이름이 일치하지 않음.

#### 해결 방법

```bash
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS,SERIAL
sudo nvme list
sudo nvme id-ctrl -v /dev/nvme1n1 | grep -i '^sn'
```

NVMe serial number와 EBS Volume ID를 대조해 대상 디스크를 확인함. 단순 디스크 순번으로 포맷 대상을 결정하지 않음.

### 3.3 `/etc/fstab` 등록 후 부팅 또는 `mount -a`가 실패함

#### 증상

```text
mount: /data/app: special device UUID=... does not exist
```

#### 원인

UUID 오타, 마운트 경로 누락, 파일시스템 타입 불일치 또는 EBS 미연결 상태가 원인임.

#### 해결 방법

```bash
sudo blkid
sudo lsblk -f
sudo mkdir -p /data/app /data/log /data/backup
sudo findmnt --verify --verbose
sudo mount -a
```

`mount -a`와 `findmnt --verify`가 정상 종료되기 전에는 재부팅하지 않음. 잘못 등록한 행은 `/etc/fstab` 백업본과 비교해 수정함.

---

## 4. 모니터링 및 알람

파일시스템 사용률은 CloudWatch Agent로 수집하고, EBS 성능은 `AWS/EBS` 지표로 확인함. 운영 볼륨은 용량 고갈과 I/O 대기 증가를 함께 감시함.

CloudWatch Agent 설정 예시:

```json
{
  "metrics": {
    "metrics_collected": {
      "disk": {
        "measurement": ["used_percent", "free"],
        "resources": ["/data/app", "/data/log", "/data/backup"],
        "ignore_file_system_types": ["sysfs", "devtmpfs", "tmpfs"]
      }
    }
  }
}
```

| 감시 대상 | 지표 | 기준 |
|---|---|---|
| 파일시스템 용량 | `disk_used_percent` | 80% 경고, 90% 위험 |
| I/O 대기 | `VolumeQueueLength` | 기준선 대비 지속 증가 시 애플리케이션 I/O와 함께 분석 |
| 읽기/쓰기 지연 | `VolumeAvgReadLatency`, `VolumeAvgWriteLatency` | 평소 p95 대비 증가 시 분석 |
| 처리량/IOPS | `VolumeReadOps`, `VolumeWriteOps`, `VolumeReadBytes`, `VolumeWriteBytes` | gp3 설정값과 인스턴스 EBS 대역폭 한계 비교 |

---

## 5. TIP

- 디스크를 업무 목적별로 `app`, `log`, `backup`으로 분리하면 로그 폭증이 애플리케이션 데이터 경로를 가득 채우는 사고를 제한할 수 있음
- LVM을 사용해도 단일 EBS 자체는 단일 장애 지점임. 데이터 경로는 EBS 스냅샷, 애플리케이션 백업, 복구 절차를 별도로 운영함
- XFS 파일시스템은 온라인 확장을 지원하지만 축소를 지원하지 않음. 볼륨 축소가 필요하면 새 파일시스템으로 데이터 이전 후 교체함
- 새 EBS 연결 후에는 포맷 전 `lsblk`, `wipefs -n`, EBS Volume ID 대조를 모두 수행함. 대상 디스크 오인식은 되돌릴 수 없는 데이터 손실로 이어짐
- 참고: [Amazon EBS를 Linux 인스턴스에 사용할 수 있도록 만들기](https://docs.aws.amazon.com/ebs/latest/userguide/ebs-using-volumes.html), [Linux 인스턴스의 EBS NVMe 볼륨](https://docs.aws.amazon.com/ebs/latest/userguide/nvme-ebs-volumes.html), [Amazon EBS 볼륨 수정](https://docs.aws.amazon.com/ebs/latest/userguide/ebs-modify-volume.html)
