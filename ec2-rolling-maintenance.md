# EC2 롤링 PM 작업 (Target Group 연동 인스턴스)

## 1. 개요

로드밸런서(ALB/NLB) **Target Group에 등록된 EC2 인스턴스**를 대상으로 계획된 유지보수(PM)를 수행할 때, 서비스 무중단을 유지하면서 AZ 단위로 순차적으로 재기동하는 방법입니다.

콘솔에서 인스턴스별로 Target Group을 하나씩 끊고 붙이는 작업은 실수가 생기기 쉽고 시간도 오래 걸립니다. AWS CLI 스크립트로 자동화하면 일관성 있게 처리할 수 있습니다.

**작업 단위 (AZ 기준 일괄 처리)**

```
[AZ1 전체 Deregister] → [드레이닝 대기] → [AZ1 전체 재기동]
    → [Healthy 확인] → [AZ3 전체 Deregister] → ... → [AZ3 Healthy 확인]
```

> AZ 내 인스턴스를 하나씩 순차 처리하면 PM 시간이 길어집니다. AZ 전체를 한 번에 내리고 한 번에 올리는 방식이 효율적이며, AZ3가 살아있는 동안 AZ1을 통째로 내려도 서비스는 유지됩니다.

---

## 2. 설명

### 2.1 핵심 개념

| 개념 | 설명 |
|------|------|
| Connection Draining (연결 드레이닝) | 인스턴스를 TG에서 제거할 때 기존 연결이 끊기지 않도록 `deregistration_delay` 시간 동안 대기 |
| `unused` 상태 | TG에서 Deregister 완료된 상태. 이 상태가 되면 트래픽이 완전히 차단됨 |
| AZ 단위 일괄 처리 | AZ1 인스턴스 전체를 동시에 TG에서 제거 → 동시에 재기동 → 동시에 재등록 |
| AZ 롤링 순서 | AZ1 완료 확인 후 AZ3 진행. 두 AZ를 동시에 작업하면 서비스 중단 발생 |

### 2.2 사전 확인

**AZ별 인스턴스 목록 조회**

```bash
# AZ1(ap-northeast-2a) running 인스턴스 목록
aws ec2 describe-instances \
  --filters "Name=placement.availability-zone,Values=ap-northeast-2a" \
            "Name=instance-state-name,Values=running" \
  --query 'Reservations[*].Instances[*].[InstanceId, Tags[?Key==`Name`].Value|[0]]' \
  --output table

# AZ3(ap-northeast-2c) running 인스턴스 목록
aws ec2 describe-instances \
  --filters "Name=placement.availability-zone,Values=ap-northeast-2c" \
            "Name=instance-state-name,Values=running" \
  --query 'Reservations[*].Instances[*].[InstanceId, Tags[?Key==`Name`].Value|[0]]' \
  --output table
```

**특정 인스턴스가 등록된 TG 목록 확인**

```bash
INSTANCE_ID="i-0123456789abcdef0"

aws elbv2 describe-target-groups \
  --query 'TargetGroups[*].TargetGroupArn' --output text \
| tr '\t' '\n' \
| while read -r TG_ARN; do
    FOUND=$(aws elbv2 describe-target-health \
      --target-group-arn "$TG_ARN" \
      --query "TargetHealthDescriptions[?Target.Id=='${INSTANCE_ID}'].Target.Id" \
      --output text)
    [ -n "$FOUND" ] && echo "$TG_ARN"
done
```

**작업 전 TG 등록 상태 백업 (복구용)**

```bash
# 모든 TG의 현재 등록 상태를 JSON으로 저장
aws elbv2 describe-target-groups --query 'TargetGroups[*].TargetGroupArn' --output text \
| tr '\t' '\n' \
| while read -r TG_ARN; do
    aws elbv2 describe-target-health \
      --target-group-arn "$TG_ARN" \
      --output json >> tg-backup-$(date +%Y%m%d-%H%M).json
done
```

---

### 2.3 AZ 단위 일괄 PM 자동화 스크립트

아래 스크립트는 **AZ 전체 Deregister → 전체 재기동 → 전체 Re-register** 순서로 처리합니다.
인스턴스별로 순차 처리하지 않고, AZ 내 모든 인스턴스를 동시에 처리합니다.

```bash
#!/bin/bash
# ec2-az-pm.sh
# 사용법: bash ec2-az-pm.sh <AZ> <instance-id> [instance-id ...]
# 예시:   bash ec2-az-pm.sh ap-northeast-2a i-0aaa i-0bbb i-0ccc

set -euo pipefail

AZ="$1"; shift
INSTANCES=("$@")

WAIT_DRAINING=60      # Connection Draining 대기(초). TG의 deregistration_delay 이상으로 설정
WAIT_HEALTH_CHECK=90  # 재등록 후 Healthy 확인 대기(초)
REGION="${AWS_DEFAULT_REGION:-ap-northeast-2}"

# 인스턴스가 등록된 TG ARN 목록 반환 (인스턴스 ID와 함께)
# 결과 형식: "<instance_id> <tg_arn>"
build_tg_map() {
    local all_tg_arns
    mapfile -t all_tg_arns < <(
        aws elbv2 describe-target-groups \
            --query 'TargetGroups[*].TargetGroupArn' \
            --output text --region "$REGION" | tr '\t' '\n'
    )

    for TG_ARN in "${all_tg_arns[@]}"; do
        for INSTANCE_ID in "${INSTANCES[@]}"; do
            FOUND=$(aws elbv2 describe-target-health \
                --target-group-arn "$TG_ARN" \
                --region "$REGION" \
                --query "TargetHealthDescriptions[?Target.Id=='${INSTANCE_ID}'].Target.Id" \
                --output text)
            if [ -n "$FOUND" ]; then
                echo "${INSTANCE_ID} ${TG_ARN}"
            fi
        done
    done
}

echo "========================================================"
echo " PM 대상 AZ : ${AZ}"
echo " 대상 인스턴스: ${INSTANCES[*]}"
echo "========================================================"

# ── Step 1. 인스턴스 → TG 매핑 수집 ──────────────────────────
echo ""
echo "[Step 1] 인스턴스별 TG 매핑 수집 중..."
declare -A INSTANCE_TGS   # instance_id -> "tg_arn1 tg_arn2 ..."

while IFS=' ' read -r INST TG; do
    if [ -n "${INSTANCE_TGS[$INST]+_}" ]; then
        INSTANCE_TGS[$INST]="${INSTANCE_TGS[$INST]} ${TG}"
    else
        INSTANCE_TGS[$INST]="${TG}"
    fi
done < <(build_tg_map)

for INST in "${INSTANCES[@]}"; do
    echo "  ${INST} → ${INSTANCE_TGS[$INST]:-등록된 TG 없음}"
done

# ── Step 2. AZ 전체 인스턴스를 모든 TG에서 일괄 Deregister ──
echo ""
echo "[Step 2] AZ(${AZ}) 전체 TG Deregister..."

for INST in "${INSTANCES[@]}"; do
    for TG_ARN in ${INSTANCE_TGS[$INST]:-}; do
        echo "  [Deregister] ${INST} ← ${TG_ARN}"
        aws elbv2 deregister-targets \
            --target-group-arn "$TG_ARN" \
            --targets "Id=${INST}" \
            --region "$REGION"
    done
done

echo "  → Deregister 완료. Connection Draining ${WAIT_DRAINING}s 대기..."
sleep "$WAIT_DRAINING"

# ── Step 3. AZ 전체 인스턴스 동시 Stop ───────────────────────
echo ""
echo "[Step 3] 인스턴스 전체 Stop..."
aws ec2 stop-instances --instance-ids "${INSTANCES[@]}" --region "$REGION" > /dev/null
echo "  → Stop 명령 전송 완료. Stopped 상태 대기..."
aws ec2 wait instance-stopped --instance-ids "${INSTANCES[@]}" --region "$REGION"
echo "  → 전체 Stopped 확인"

# ── Step 4. AZ 전체 인스턴스 동시 Start ──────────────────────
echo ""
echo "[Step 4] 인스턴스 전체 Start..."
aws ec2 start-instances --instance-ids "${INSTANCES[@]}" --region "$REGION" > /dev/null
echo "  → Start 명령 전송 완료. Running 상태 대기..."
aws ec2 wait instance-running --instance-ids "${INSTANCES[@]}" --region "$REGION"
echo "  → 전체 Running 확인"

# ── Step 5. AZ 전체 인스턴스를 모든 TG에 일괄 Re-register ───
echo ""
echo "[Step 5] AZ(${AZ}) 전체 TG Re-register..."

for INST in "${INSTANCES[@]}"; do
    for TG_ARN in ${INSTANCE_TGS[$INST]:-}; do
        echo "  [Register] ${INST} → ${TG_ARN}"
        aws elbv2 register-targets \
            --target-group-arn "$TG_ARN" \
            --targets "Id=${INST}" \
            --region "$REGION"
    done
done

echo "  → Register 완료. Health Check ${WAIT_HEALTH_CHECK}s 대기..."
sleep "$WAIT_HEALTH_CHECK"

# ── Step 6. Health Check 최종 확인 ───────────────────────────
echo ""
echo "[Step 6] TG Health 상태 최종 확인..."

FAIL=0
for INST in "${INSTANCES[@]}"; do
    for TG_ARN in ${INSTANCE_TGS[$INST]:-}; do
        STATUS=$(aws elbv2 describe-target-health \
            --target-group-arn "$TG_ARN" \
            --targets "Id=${INST}" \
            --query 'TargetHealthDescriptions[0].TargetHealth.State' \
            --output text --region "$REGION")
        if [ "$STATUS" = "healthy" ]; then
            echo "  [OK]   ${INST} → healthy (${TG_ARN##*/})"
        else
            echo "  [WARN] ${INST} → ${STATUS} (${TG_ARN##*/}) ← 수동 확인 필요"
            FAIL=1
        fi
    done
done

echo ""
echo "========================================================"
if [ "$FAIL" -eq 0 ]; then
    echo " AZ(${AZ}) PM 완료 — 전체 healthy"
else
    echo " AZ(${AZ}) PM 완료 — 일부 unhealthy 확인 필요"
fi
echo "========================================================"
```

---

### 2.4 실행 순서 (AZ1 → AZ3 롤링)

```bash
# ① AZ1 먼저 실행
bash ec2-az-pm.sh ap-northeast-2a i-0aaa111 i-0bbb222 i-0ccc333

# ② AZ1 전체 healthy 확인 후 AZ3 실행
bash ec2-az-pm.sh ap-northeast-2c i-0ddd444 i-0eee555 i-0fff666
```

실행 흐름 요약:

```
AZ1: [전체 TG Deregister] ──드레이닝대기──> [전체 Stop/Start] ──헬스체크──> [전체 TG Register] ──> healthy 확인
                                                                                                         ↓
AZ3:                                                                          [전체 TG Deregister] ──> ...
```

> AZ3 인스턴스가 살아있는 상태에서 AZ1을 통째로 내리기 때문에 서비스 무중단이 보장됩니다.

---

### 2.5 boto3 기반 파일 스냅샷 방식 (Deregister / Register 분리 실행)

기존 스크립트(`ec2-az-pm.sh`)는 인스턴스 목록을 직접 파라미터로 넘겨야 합니다.
아래 Python 스크립트는 **"현재 TG 등록 상태를 JSON 파일로 저장"** 한 뒤, 해당 파일을 근거로 Deregister와 Register를 별도 타이밍에 실행할 수 있습니다.
PM 절차를 단계별로 나눠서 승인 받아가며 진행할 때 특히 유용합니다.

**사용 흐름**

```
① snapshot  →  ② deregister  →  (PM 작업)  →  ③ register  →  ④ status
```

**스크립트: `tg-az-ctrl.py`**

```python
#!/usr/bin/env python3
"""
tg-az-ctrl.py — Target Group AZ별 등록 상태 스냅샷 / Deregister / Register

사용법:
  python tg-az-ctrl.py snapshot   --az ap-northeast-2a [--output tg-state.json]
  python tg-az-ctrl.py deregister --file tg-state.json
  python tg-az-ctrl.py register   --file tg-state.json
  python tg-az-ctrl.py status     --file tg-state.json

의존성: boto3 (pip install boto3)
"""
import argparse
import json
import sys
from datetime import datetime, timezone

import boto3


# ──────────────────────────────────────────
# 내부 헬퍼
# ──────────────────────────────────────────

def _get_az_instances(ec2, az: str) -> list:
    """특정 AZ의 running 인스턴스 ID 목록을 반환 (페이지네이션 처리)"""
    paginator = ec2.get_paginator('describe_instances')
    ids = []
    for page in paginator.paginate(
        Filters=[
            {'Name': 'placement.availability-zone', 'Values': [az]},
            {'Name': 'instance-state-name', 'Values': ['running']},
        ]
    ):
        for r in page['Reservations']:
            for inst in r['Instances']:
                ids.append(inst['InstanceId'])
    return ids


def _get_all_tg_arns(elb) -> list:
    """계정 내 모든 TG ARN 목록 (페이지네이션 처리)"""
    paginator = elb.get_paginator('describe_target_groups')
    arns = []
    for page in paginator.paginate():
        for tg in page['TargetGroups']:
            arns.append(tg['TargetGroupArn'])
    return arns


def _tg_short_name(arn: str) -> str:
    """arn:aws:...:targetgroup/my-tg/abc → my-tg"""
    return arn.split('/')[-2]


# ──────────────────────────────────────────
# snapshot 서브커맨드
# ──────────────────────────────────────────

def cmd_snapshot(args):
    session = boto3.session.Session(region_name=args.region)
    elb = session.client('elbv2')
    ec2 = session.client('ec2')

    print(f"[snapshot] AZ={args.az} running 인스턴스 조회 중...")
    instance_ids = _get_az_instances(ec2, args.az)
    if not instance_ids:
        print(f"[WARN] AZ {args.az} 에 running 인스턴스가 없습니다.")
        sys.exit(1)

    instance_set = set(instance_ids)
    print(f"[snapshot] 인스턴스 {len(instance_ids)}개 발견. TG 전체 스캔 중...")

    # instance_id → [{tg_arn, tg_name, port}]
    target_map = {iid: [] for iid in instance_ids}

    for tg_arn in _get_all_tg_arns(elb):
        resp = elb.describe_target_health(TargetGroupArn=tg_arn)
        for entry in resp['TargetHealthDescriptions']:
            t = entry['Target']
            if t['Id'] in instance_set:
                target_map[t['Id']].append({
                    'tg_arn': tg_arn,
                    'tg_name': _tg_short_name(tg_arn),
                    'port': t.get('Port'),   # None = TG 기본 포트
                })

    snapshot = {
        'az': args.az,
        'region': args.region,
        'saved_at': datetime.now(timezone.utc).isoformat(),
        'targets': [
            {'instance_id': iid, 'tg_registrations': regs}
            for iid, regs in target_map.items()
        ],
    }

    outfile = args.output or f"tg-state-{args.az}-{datetime.now().strftime('%Y%m%d-%H%M')}.json"
    with open(outfile, 'w', encoding='utf-8') as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)

    total_regs = sum(len(t['tg_registrations']) for t in snapshot['targets'])
    print(f"[snapshot] 저장 완료 → {outfile}")
    print(f"  인스턴스 {len(snapshot['targets'])}개 / TG 등록 {total_regs}건")
    for t in snapshot['targets']:
        for reg in t['tg_registrations']:
            port_str = str(reg['port']) if reg['port'] else 'default'
            print(f"  {t['instance_id']} → {reg['tg_name']} (port={port_str})")


# ──────────────────────────────────────────
# deregister 서브커맨드
# ──────────────────────────────────────────

def cmd_deregister(args):
    with open(args.file, encoding='utf-8') as f:
        snapshot = json.load(f)

    session = boto3.session.Session(region_name=snapshot['region'])
    elb = session.client('elbv2')

    print(f"[deregister] AZ={snapshot['az']}  (스냅샷: {snapshot['saved_at']})")
    for t in snapshot['targets']:
        for reg in t['tg_registrations']:
            target = {'Id': t['instance_id']}
            if reg['port']:
                target['Port'] = reg['port']
            print(f"  [Deregister] {t['instance_id']} ← {reg['tg_name']}")
            elb.deregister_targets(
                TargetGroupArn=reg['tg_arn'],
                Targets=[target],
            )

    print("[deregister] 완료.")
    print("  → Connection Draining(deregistration_delay) 대기 후 PM 작업을 진행하세요.")
    print(f"  → 복구 시: python tg-az-ctrl.py register --file {args.file}")


# ──────────────────────────────────────────
# register 서브커맨드
# ──────────────────────────────────────────

def cmd_register(args):
    with open(args.file, encoding='utf-8') as f:
        snapshot = json.load(f)

    session = boto3.session.Session(region_name=snapshot['region'])
    elb = session.client('elbv2')

    print(f"[register] AZ={snapshot['az']}  (스냅샷: {snapshot['saved_at']})")
    for t in snapshot['targets']:
        for reg in t['tg_registrations']:
            target = {'Id': t['instance_id']}
            if reg['port']:
                target['Port'] = reg['port']
            print(f"  [Register] {t['instance_id']} → {reg['tg_name']}")
            elb.register_targets(
                TargetGroupArn=reg['tg_arn'],
                Targets=[target],
            )

    print("[register] 완료.")
    print(f"  → 상태 확인: python tg-az-ctrl.py status --file {args.file}")


# ──────────────────────────────────────────
# status 서브커맨드
# ──────────────────────────────────────────

def cmd_status(args):
    with open(args.file, encoding='utf-8') as f:
        snapshot = json.load(f)

    session = boto3.session.Session(region_name=snapshot['region'])
    elb = session.client('elbv2')

    print(f"[status] AZ={snapshot['az']}  (스냅샷: {snapshot['saved_at']})")
    all_ok = True
    for t in snapshot['targets']:
        for reg in t['tg_registrations']:
            resp = elb.describe_target_health(
                TargetGroupArn=reg['tg_arn'],
                Targets=[{'Id': t['instance_id']}],
            )
            descs = resp['TargetHealthDescriptions']
            state = descs[0]['TargetHealth']['State'] if descs else 'not-registered'
            mark = 'OK  ' if state == 'healthy' else 'WARN'
            if state != 'healthy':
                all_ok = False
            print(f"  [{mark}] {t['instance_id']} → {reg['tg_name']} : {state}")

    print()
    if all_ok:
        print("[status] 전체 healthy ✓")
    else:
        print("[status] 일부 unhealthy — 수동 확인 필요")
        sys.exit(1)


# ──────────────────────────────────────────
# 진입점
# ──────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Target Group AZ별 등록 상태를 파일로 관리합니다.'
    )
    parser.add_argument('--region', default='ap-northeast-2', help='AWS 리전 (기본: ap-northeast-2)')
    sub = parser.add_subparsers(dest='cmd', required=True)

    # snapshot
    p = sub.add_parser('snapshot', help='AZ의 TG 등록 상태를 JSON 파일로 저장')
    p.add_argument('--az', required=True, help='예: ap-northeast-2a')
    p.add_argument('--output', help='출력 파일 경로 (기본: tg-state-<az>-<timestamp>.json)')
    p.set_defaults(func=cmd_snapshot)

    # deregister
    p = sub.add_parser('deregister', help='스냅샷 파일 기반으로 TG에서 Deregister')
    p.add_argument('--file', required=True, help='snapshot 명령으로 생성한 JSON 파일 경로')
    p.set_defaults(func=cmd_deregister)

    # register
    p = sub.add_parser('register', help='스냅샷 파일 기반으로 TG에 Register')
    p.add_argument('--file', required=True, help='snapshot 명령으로 생성한 JSON 파일 경로')
    p.set_defaults(func=cmd_register)

    # status
    p = sub.add_parser('status', help='스냅샷 파일 기반으로 현재 TG Health 확인')
    p.add_argument('--file', required=True, help='snapshot 명령으로 생성한 JSON 파일 경로')
    p.set_defaults(func=cmd_status)

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
```

**실행 예시 — AZ1 PM 전체 흐름**

```bash
# 1. 사전 스냅샷 (TG 등록 상태 파일로 저장)
python tg-az-ctrl.py snapshot --az ap-northeast-2a
#  → tg-state-ap-northeast-2a-20260415-1430.json 생성

# 2. AZ1 전체 TG Deregister
python tg-az-ctrl.py deregister --file tg-state-ap-northeast-2a-20260415-1430.json
#  → Connection Draining 대기 후 PM 진행

# 3. (PM 작업: Stop/Start, 커널 패치 등)
aws ec2 stop-instances  --instance-ids i-0aaa111 i-0bbb222
aws ec2 wait instance-stopped --instance-ids i-0aaa111 i-0bbb222
aws ec2 start-instances --instance-ids i-0aaa111 i-0bbb222
aws ec2 wait instance-running  --instance-ids i-0aaa111 i-0bbb222

# 4. AZ1 전체 TG Re-register (스냅샷 파일 기반)
python tg-az-ctrl.py register --file tg-state-ap-northeast-2a-20260415-1430.json

# 5. Health 상태 확인
python tg-az-ctrl.py status   --file tg-state-ap-northeast-2a-20260415-1430.json

# 6. AZ3 동일하게 반복
python tg-az-ctrl.py snapshot   --az ap-northeast-2c
python tg-az-ctrl.py deregister --file tg-state-ap-northeast-2c-<timestamp>.json
# ... PM ...
python tg-az-ctrl.py register   --file tg-state-ap-northeast-2c-<timestamp>.json
python tg-az-ctrl.py status     --file tg-state-ap-northeast-2c-<timestamp>.json
```

**저장 파일 형식 (`tg-state-*.json`)**

```json
{
  "az": "ap-northeast-2a",
  "region": "ap-northeast-2",
  "saved_at": "2026-04-15T14:30:00+00:00",
  "targets": [
    {
      "instance_id": "i-0aaa111",
      "tg_registrations": [
        {
          "tg_arn": "arn:aws:elasticloadbalancing:ap-northeast-2:123456789012:targetgroup/my-tg/abc123",
          "tg_name": "my-tg",
          "port": null
        }
      ]
    }
  ]
}
```

> `port: null` 은 TG 기본 포트를 사용한다는 의미입니다.
> 포트 오버라이드로 등록된 타겟은 `port` 값이 정수로 저장되어 Register 시 그대로 복원됩니다.

**기존 bash 스크립트(`ec2-az-pm.sh`)와의 차이점**

| 항목 | `ec2-az-pm.sh` | `tg-az-ctrl.py` |
|------|---------------|-----------------|
| 인스턴스 지정 | 파라미터로 직접 전달 | AZ 기반 자동 탐색 |
| Stop/Start | 스크립트 내 포함 | 별도 실행 (유연성) |
| Deregister/Register | 하나의 흐름으로 연결 | 단계별 분리 실행 가능 |
| 상태 저장 | 없음 | JSON 파일로 영구 보관 |
| 포트 오버라이드 | 미지원 | 자동 감지 및 복원 |

---

### 2.6 ASG(Auto Scaling Group)에 속한 인스턴스인 경우

ASG 인스턴스는 Stop 시 **자동으로 Terminate**될 수 있으므로 Standby 상태 전환이 필수입니다.
Standby 전환 시 TG 해제도 자동으로 처리되므로 위 스크립트 대신 아래 방법을 사용합니다.

```bash
ASG_NAME="my-asg-name"
# AZ1 인스턴스 목록
AZ1_INSTANCES=(i-0aaa111 i-0bbb222 i-0ccc333)

# 1. AZ1 전체 Standby 전환 (TG 자동 해제 + 드레이닝 포함)
aws autoscaling enter-standby \
  --instance-ids "${AZ1_INSTANCES[@]}" \
  --auto-scaling-group-name "$ASG_NAME" \
  --should-decrement-desired-capacity

# 2. Standby 확인
aws autoscaling describe-auto-scaling-instances \
  --instance-ids "${AZ1_INSTANCES[@]}" \
  --query 'AutoScalingInstances[*].[InstanceId, LifecycleState]' \
  --output table

# 3. 전체 Stop → Start
aws ec2 stop-instances --instance-ids "${AZ1_INSTANCES[@]}"
aws ec2 wait instance-stopped --instance-ids "${AZ1_INSTANCES[@]}"
aws ec2 start-instances --instance-ids "${AZ1_INSTANCES[@]}"
aws ec2 wait instance-running --instance-ids "${AZ1_INSTANCES[@]}"

# 4. InService 복귀 (TG 자동 재등록)
aws autoscaling exit-standby \
  --instance-ids "${AZ1_INSTANCES[@]}" \
  --auto-scaling-group-name "$ASG_NAME"
```

> ASG 환경에서는 `enter-standby` / `exit-standby`로 TG 등록/해제가 자동 처리됩니다.
> `ec2-az-pm.sh` 스크립트는 ASG 없이 독립 실행형 EC2에 적합합니다.

---

### 2.7 보안/비용 Best Practice

- **deregistration_delay 확인 및 임시 단축** — 기본값 300초. PM 창이 좁다면 작업 전 60초로 줄이고 완료 후 원복
  ```bash
  aws elbv2 modify-target-group-attributes \
    --target-group-arn <TG_ARN> \
    --attributes Key=deregistration_delay.timeout_seconds,Value=60
  ```
- **작업 전 스냅샷** — 루트 볼륨 스냅샷을 미리 생성해두면 이슈 발생 시 빠른 복구 가능 (`ec2-snapshot-root-volume-recovery.md` 참고)
- **IAM 권한 최소화** — 스크립트 실행 계정에 아래 권한만 부여
  - `elasticloadbalancing:DescribeTargetGroups`
  - `elasticloadbalancing:DescribeTargetHealth`
  - `elasticloadbalancing:DeregisterTargets`
  - `elasticloadbalancing:RegisterTargets`
  - `ec2:StopInstances`
  - `ec2:StartInstances`
  - `ec2:DescribeInstances`

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**Deregister 후에도 트래픽이 계속 들어옴**
- 증상: TG에서 제거했는데 인스턴스 로그에 요청이 계속 찍힘
- 원인: `deregistration_delay` 시간 내 기존 연결(Keep-Alive)이 유지됨
- 해결: 스크립트의 `WAIT_DRAINING` 값을 TG의 `deregistration_delay` 이상으로 설정

**Start 후 Healthy가 되지 않음**
- 증상: Register 후 TG 상태가 `initial` 또는 `unhealthy`
- 원인: 애플리케이션 기동 시간이 Health Check 주기보다 길거나 Security Group 설정 문제
- 해결: `WAIT_HEALTH_CHECK` 값 증가, Health Check 경로/포트 확인

**ASG가 Standby 인스턴스 자리를 새 인스턴스로 채움**
- 증상: Standby 전환 후 새 인스턴스가 추가로 생성됨
- 원인: `--should-decrement-desired-capacity` 옵션 누락
- 해결: `enter-standby` 시 반드시 해당 옵션 포함

### 3.2 자주 발생하는 문제 (Q&A)

**Q: 인스턴스가 여러 TG에 동시에 등록되어 있는데 한 TG만 제거해도 되나요?**
- A: 모든 TG에서 제거해야 합니다. 하나라도 남아있으면 그 LB를 통한 트래픽이 계속 인입됩니다. 스크립트는 자동으로 전체 TG를 탐지하여 처리합니다.

**Q: AZ1 작업 중 AZ3만으로 트래픽을 감당할 수 있나요?**
- A: LB가 AZ1 타겟을 모두 제거하면 AZ3로만 라우팅됩니다. 사전에 AZ3 인스턴스의 용량이 전체 트래픽을 처리할 수 있는지 확인해야 합니다.

**Q: ALB와 NLB에 동시에 등록된 경우도 처리되나요?**
- A: 네, 스크립트는 `describe-target-groups`로 ALB/NLB 구분 없이 모든 TG를 탐색합니다.

---

## 4. 모니터링 및 알람

**PM 진행 중 Target Group 상태 실시간 확인**

```bash
TG_ARN="arn:aws:elasticloadbalancing:ap-northeast-2:123456789012:targetgroup/my-tg/abc123"

watch -n 5 "aws elbv2 describe-target-health \
  --target-group-arn $TG_ARN \
  --query 'TargetHealthDescriptions[*].[Target.Id, TargetHealth.State]' \
  --output table"
```

**CloudWatch ALB 지표 확인 (PM 중 에러율 모니터링)**

```bash
# 5XX 에러 수 조회 (최근 30분, macOS date 기준)
aws cloudwatch get-metric-statistics \
  --namespace AWS/ApplicationELB \
  --metric-name HTTPCode_Target_5XX_Count \
  --dimensions Name=LoadBalancer,Value=<ALB_이름> \
  --start-time $(date -u -v-30M +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 60 \
  --statistics Sum
```

**HealthyHostCount 지표로 AZ별 healthy 인스턴스 수 확인**

```bash
# AZ1 작업 중 AZ3 HealthyHostCount 확인
aws cloudwatch get-metric-statistics \
  --namespace AWS/ApplicationELB \
  --metric-name HealthyHostCount \
  --dimensions Name=LoadBalancer,Value=<ALB_이름> \
                Name=TargetGroup,Value=<TG_이름> \
                Name=AvailabilityZone,Value=ap-northeast-2c \
  --start-time $(date -u -v-10M +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 60 \
  --statistics Average
```

---

## 5. TIP

- **작업 전 TG 상태 백업**: 등록 상태를 파일로 저장해두면 롤백 시 재등록 목록으로 활용 가능
  ```bash
  aws elbv2 describe-target-health --target-group-arn <TG_ARN> \
    --output json > tg-backup-$(date +%Y%m%d-%H%M).json
  ```
- **AZ3 용량 사전 확인**: AZ1을 전부 내리기 전에 AZ3 인스턴스만으로 트래픽을 감당할 수 있는지 부하 지표 확인
- **deregistration_delay 임시 단축**: PM 창이 좁다면 작업 전 60초로 줄이고 완료 후 원복
- **관련 문서**:
  - `ec2-autoscaling-stop-start.md` — ASG Standby 상세
  - `ec2-snapshot-root-volume-recovery.md` — PM 전 스냅샷 생성
  - `nlb-ec2-port-forwarding.md` — NLB Target Group 구성
