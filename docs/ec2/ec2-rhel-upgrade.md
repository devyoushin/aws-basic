# RHEL dnf 업그레이드 — 마이너 버전 및 8→9 메이저 업그레이드

## 1. 개요

RHEL(Red Hat Enterprise Linux)은 `dnf` 패키지 매니저를 통해 마이너 버전(8.6→8.9 등)을
업그레이드하고, 메이저 버전(8→9) 업그레이드는 **Leapp** 업그레이드 프레임워크를 사용한다.
EC2 RHEL 인스턴스 운영 시 보안 패치, EOL 대응, OS 현대화를 위해 반드시 숙지해야 한다.

**핵심 요약**
- **마이너 업그레이드**: `dnf update` + `subscription-manager release` 로 버전 고정/해제
- **메이저 업그레이드**: `leapp preupgrade` → 억제 요인(Inhibitor) 해결 → `leapp upgrade` → 재부팅
- **관련 서비스**: EC2, SSM Patch Manager, Systems Manager, AWS Backup

---

## 2. 설명

### 2.1 핵심 개념

**RHEL 버전 체계**

```
RHEL 8.x (예: 8.6, 8.7, 8.8, 8.9, 8.10)  ← 마이너 업그레이드 (dnf)
   ↓ Leapp 업그레이드
RHEL 9.x (예: 9.0, 9.1, 9.2, 9.3, 9.4)  ← 메이저 업그레이드
```

| 항목 | 마이너 업그레이드 | 메이저 업그레이드 |
|------|----------------|----------------|
| 도구 | `dnf update` | `leapp upgrade` |
| 재부팅 | 커널 업데이트 시만 필요 | 필수 (여러 번) |
| 다운타임 | 최소 (rolling 가능) | 필수 (단일 인스턴스 기준) |
| 위험도 | 낮음 | 높음 — 사전 검증 필수 |
| 롤백 | dnf history undo / 스냅샷 | 스냅샷 복원만 가능 |

**subscription-manager 릴리스 잠금(Lock) 개념**

```
잠금 없음(기본):  dnf update → 현재 메이저의 최신 마이너로 자동 업데이트
잠금 설정:       subscription-manager release --set=8.6
                 dnf update → 8.6 패키지만 업데이트 (8.7+ 패키지 설치 안 됨)
```

---

### 2.2 마이너 버전 업그레이드 (RHEL 8.x → 8.y)

#### 현재 버전 확인

```bash
# OS 버전 확인
cat /etc/redhat-release
# Red Hat Enterprise Linux release 8.6 (Ootpa)

# 커널 버전 확인
uname -r

# 현재 릴리스 잠금 상태 확인
subscription-manager release --show
# Release: 8.6  (잠금 설정됨) 또는 "not set" (잠금 없음)
```

#### 릴리스 잠금 해제 후 최신 마이너로 업그레이드

```bash
# 1. 업그레이드 전 EBS 스냅샷 생성 (필수)
INSTANCE_ID=$(curl -s http://169.254.169.254/latest/meta-data/instance-id)
ROOT_VOL=$(aws ec2 describe-instances \
  --instance-ids $INSTANCE_ID \
  --query 'Reservations[0].Instances[0].BlockDeviceMappings[?DeviceName==`/dev/xvda`].Ebs.VolumeId' \
  --output text \
  --region ap-northeast-2)

aws ec2 create-snapshot \
  --volume-id $ROOT_VOL \
  --description "pre-upgrade-rhel8-$(date +%Y%m%d)" \
  --region ap-northeast-2

# 2. 릴리스 잠금 해제 (최신 마이너로 업그레이드 허용)
subscription-manager release --unset

# 3. DNF 캐시 초기화
dnf clean all

# 4. 패키지 목록 확인 (dry-run)
dnf update --assumeno

# 5. 업그레이드 실행
dnf update -y

# 6. 재부팅 (커널 업데이트 포함 시)
reboot

# 7. 업그레이드 결과 확인
cat /etc/redhat-release
rpm -qa --last | head -20  # 최근 업데이트된 패키지 확인
```

#### 특정 마이너 버전으로 잠금 업그레이드 (예: 8.6 → 8.9)

```bash
# 1. 목표 릴리스 잠금 설정
subscription-manager release --set=8.9

# 2. 확인
subscription-manager release --show
# Release: 8.9

# 3. 업그레이드 실행
dnf update -y

# 4. 재부팅
reboot

# 5. 결과 확인
cat /etc/redhat-release
# Red Hat Enterprise Linux release 8.9 (Ootpa)
```

#### dnf history로 변경 사항 추적 및 롤백

```bash
# 업그레이드 히스토리 확인
dnf history list

# 특정 트랜잭션 상세 확인
dnf history info <ID>

# 롤백 (특정 트랜잭션 이전 상태로 되돌리기)
dnf history undo <ID>

# 모든 패키지를 특정 날짜로 롤백
dnf history rollback <ID>
```

#### SSM Patch Manager를 활용한 자동 패치 (EC2)

```bash
# Patch Baseline 생성 (Security 패치만 자동 적용)
aws ssm create-patch-baseline \
  --name "RHEL8-SecurityOnly" \
  --operating-system "REDHAT_ENTERPRISE_LINUX" \
  --approval-rules '{"PatchRules":[{"PatchFilterGroup":{"PatchFilters":[{"Key":"CLASSIFICATION","Values":["Security"]},{"Key":"SEVERITY","Values":["Critical","Important"]}]},"ApproveAfterDays":7}]}' \
  --region ap-northeast-2

# Patch Group에 인스턴스 등록
aws ssm add-tags-to-resource \
  --resource-type "ManagedInstance" \
  --resource-id "mi-xxxxxxxx" \
  --tags "Key=Patch Group,Value=rhel8-prod" \
  --region ap-northeast-2
```

---

### 2.3 메이저 버전 업그레이드 (RHEL 8 → 9) — Leapp

#### 전제 조건 확인

```bash
# RHEL 8 최신 마이너 버전 여부 확인 (8.8 이상 권장)
cat /etc/redhat-release

# 최신 마이너로 먼저 업그레이드
subscription-manager release --unset
dnf update -y
reboot

# 구독 상태 확인
subscription-manager status
subscription-manager list --installed

# 여유 디스크 공간 확인 (/ 파티션 최소 5GB 필요)
df -h /
df -h /boot   # /boot 최소 500MB 필요

# 활성화된 서드파티 레포 확인 (Leapp 억제 원인이 됨)
dnf repolist
```

#### Leapp 설치 및 사전 검증 (preupgrade)

```bash
# 1. Leapp 설치
dnf install leapp-upgrade -y

# 2. 사전 검증 실행 (실제 업그레이드 안 함 — 문제만 분석)
leapp preupgrade --target 9.4

# 3. 보고서 확인 (억제 요인 Inhibitor / 경고 Warning 분류)
leapp report  # 또는
cat /var/log/leapp/leapp-report.txt

# 억제 요인(Inhibitor) 예시:
# - Detected loaded kernel module(s): e1000 (업그레이드 불가 드라이버)
# - Missing required answers in the answer file
# - PAM module configuration incompatibility
```

#### 주요 억제 요인별 해결

```bash
# [억제 요인 1] PAM PKCS#11 모듈 — 비활성화 확인 응답
leapp answer --section remove_pam_pkcs11_module_check.confirm=True

# [억제 요인 2] VDO (Virtual Data Optimizer) 비활성화
# VDO 볼륨 사용 중이면 LVM 통합 VDO로 전환 필요

# [억제 요인 3] 서드파티 레포 비활성화
dnf config-manager --disable <repo-id>
# 예: dnf config-manager --disable epel

# [억제 요인 4] 오래된 드라이버 제거
# 보고서에서 지정한 커널 모듈 언로드
modprobe -r <module_name>

# [억제 요인 5] 구형 crypto 정책
update-crypto-policies --set DEFAULT

# [경고] 서드파티 패키지 — 업그레이드 후 수동 재설치 필요 목록 확인
grep "third-party" /var/log/leapp/leapp-report.txt
```

#### 업그레이드 실행

```bash
# 1. 재차 preupgrade로 Inhibitor 없음 확인
leapp preupgrade --target 9.4
# 결과에 "Inhibitor" 없어야 함

# 2. 업그레이드 시작 (재부팅 포함, 약 30~60분 소요)
leapp upgrade --target 9.4

# → 1차 재부팅: Leapp initramfs 환경에서 패키지 교체
# → 2차 재부팅: RHEL 9 커널로 부팅
```

#### 업그레이드 후 검증

```bash
# OS 버전 확인
cat /etc/redhat-release
# Red Hat Enterprise Linux release 9.4 (Plow)

uname -r
# 5.14.0-xxx.el9.x86_64

# 구독 상태 확인
subscription-manager list --installed

# 서비스 상태 확인
systemctl list-units --state=failed

# 서드파티 패키지 재설치 필요 목록 확인
rpm -qa | grep -v "\.el9" | grep -v "\.noarch"

# DNF 레포 재활성화 (RHEL 9용으로 업데이트 후)
dnf repolist

# 남은 RHEL 8 패키지 정리
dnf remove $(dnf repoquery --extras --queryformat="%{name}") -y
```

#### RHEL 8 → 9 주요 변경사항 대응

| 항목 | RHEL 8 | RHEL 9 | 대응 방법 |
|------|--------|--------|----------|
| 기본 Python | 3.6 | 3.9 | 앱 호환성 확인, `python3.9` 명령 사용 |
| OpenSSL | 1.1.1 | 3.0 | TLS 설정 재검토, 구형 암호 비활성화 |
| Crypto Policy | DEFAULT | DEFAULT (더 엄격) | `update-crypto-policies --set LEGACY` 임시 적용 가능 |
| iptables | legacy + nftables | nftables 기본 | 방화벽 규칙 검토 |
| SSH RSA 키 | SHA-1 허용 | SHA-1 비허용 | `ssh-keygen -t ed25519` 재발급 |
| systemd | 239 | 250 | Unit 파일 호환성 확인 |

---

### 2.4 보안/비용 Best Practice

**보안**
- 업그레이드 전 반드시 **EBS 스냅샷** 생성 — Leapp 실패 시 유일한 롤백 수단
- RHEL 9 기본 Crypto Policy는 RHEL 8보다 엄격 — 레거시 TLS 1.0/1.1 차단
- SELinux Enforcing 모드 활성화 권장 (RHEL 9 기본: Enforcing)

**비용**
- EC2에서 RHEL 업그레이드 시 AMI 교체(새 인스턴스)가 Leapp보다 빠르고 안전
- **Golden AMI 파이프라인** 구축 후 새 AMI로 인스턴스 교체 방식 권장 (대규모 플릿)
- 단일 인스턴스 업그레이드는 검증 환경에서 먼저 테스트 후 프로덕션 적용

---

## 3. 트러블슈팅

### 3.1 주요 이슈

#### Leapp preupgrade — "No suitable target found"

**증상**
- `leapp preupgrade` 실행 시 대상 버전을 찾지 못함

**원인**
- RHEL 구독이 만료되었거나 업그레이드 경로가 미등록

**해결 방법**
```bash
# 구독 상태 확인
subscription-manager status
subscription-manager refresh

# 업그레이드 경로 데이터 업데이트
leapp update

# Leapp 재설치
dnf reinstall leapp-upgrade -y
```

#### dnf update 후 부팅 실패 — 커널 패닉

**증상**
- 업그레이드 후 재부팅 시 커널 패닉 또는 initramfs 드롭

**원인**
- 커스텀 커널 모듈(드라이버)이 새 커널과 호환되지 않음

**해결 방법**
```bash
# GRUB에서 이전 커널로 부팅 (재부팅 시 Shift/Esc 키로 GRUB 진입)
# 또는 EC2 콘솔 → EC2 Serial Console → GRUB 선택

# 이전 커널 목록 확인
grubby --info=ALL | grep kernel

# 기본 부팅 커널 변경 (인덱스 0이 최신)
grubby --set-default-index=1

# 문제 커널 제거
dnf remove kernel-<버전>
```

#### Leapp 업그레이드 중단 — 1차 재부팅 후 멈춤

**증상**
- `leapp upgrade` 후 1차 재부팅 진행 중 initramfs 환경에서 멈춤

**원인**
- 디스크 공간 부족, 패키지 충돌

**해결 방법**
```bash
# EC2 Serial Console로 접속 후 로그 확인
cat /var/log/leapp/leapp-upgrade.log

# 디스크 공간 확인
df -h

# /boot 공간 확보 (구 커널 제거)
package-cleanup --oldkernels --count=1

# Leapp 재시도
leapp upgrade --target 9.4
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: EC2 RHEL 인스턴스에서 subscription-manager가 없으면?**
A: AWS Marketplace의 RHEL AMI는 RHUI(Red Hat Update Infrastructure)를 사용하므로
`subscription-manager` 없이 `dnf update`가 가능합니다.
`/etc/yum.repos.d/redhat-rhui.repo` 파일이 있으면 RHUI 환경입니다.
Leapp 업그레이드 시에도 RHUI 기반으로 동작합니다.

**Q: RHEL 8 → 9 Leapp 대신 AMI 교체를 언제 선택하나요?**
A: 프로덕션 플릿(노드 10대 이상)이면 Leapp보다 새 RHEL 9 AMI로 인스턴스 교체가
안전합니다. Leapp는 단일/소수 인스턴스 업그레이드, 또는 상태(State)가 많은 DB 서버처럼
IP/호스트명 유지가 중요할 때 사용합니다.

**Q: dnf update와 dnf upgrade의 차이?**
A: RHEL 8+ 에서는 동일하게 동작합니다. `dnf upgrade`는 `dnf update --obsoletes`의
별칭이며, 더 이상 사용되지 않는 패키지를 제거하는 점만 다릅니다. 운영 서버에서는
`dnf update` 사용을 권장합니다.

---

## 4. 모니터링 및 알람

### CloudWatch 핵심 지표

| 지표 | 네임스페이스 | 의미 | 임계값 예시 |
|------|-------------|------|------------|
| `disk_used_percent` | `CWAgent` | / 파티션 사용률 (Leapp 공간 필요) | `> 80%` |
| `StatusCheckFailed_System` | `AWS/EC2` | 시스템 상태 체크 실패 | `>= 1` |

### 업그레이드 전후 상태 알람

```bash
# 업그레이드 전: 루트 파티션 여유 공간 알람
aws cloudwatch put-metric-alarm \
  --alarm-name "rhel-upgrade-disk-check" \
  --alarm-description "RHEL 업그레이드 전 디스크 여유 공간 부족" \
  --metric-name "disk_used_percent" \
  --namespace "CWAgent" \
  --dimensions Name=InstanceId,Value=i-xxxxxxxxxxxxxxxxx Name=path,Value=/ \
  --statistic Average \
  --period 300 \
  --evaluation-periods 1 \
  --threshold 80 \
  --comparison-operator GreaterThanThreshold \
  --alarm-actions "arn:aws:sns:ap-northeast-2:123456789012:<SNS_TOPIC>" \
  --region ap-northeast-2
```

---

## 5. TIP

- **Leapp 업그레이드 전 체크리스트**: EBS 스냅샷 → `leapp preupgrade` → Inhibitor 0개 확인 → `leapp upgrade`
- **RHUI 환경(EC2 AWS Marketplace RHEL)**: `dnf update` 및 Leapp 모두 별도 구독 없이 동작
- **대규모 플릿(Auto Scaling Group)**: Leapp 대신 새 RHEL 9 Golden AMI 빌드 → Launch Template 버전 업데이트 → Instance Refresh 권장
- **`/boot` 파티션 분리 환경**: Leapp 실행 전 `df -h /boot` 확인 필수 — 500MB 미만이면 구 커널 제거 후 진행
- **Leapp 억제 요인 사전 확인**: `leapp preupgrade` 결과를 Inhibitor/Warning/Info 3단계로 분류 — Inhibitor만 해결하면 업그레이드 진행 가능

**관련 문서**
- 연관 내부 문서: `docs/ec2/ec2-al2-al2023.md`, `docs/ec2/ec2-snapshot-root-volume-recovery.md`, `docs/ec2/ec2-ami-management.md`
