# AL2 → AL2023 마이그레이션 (IP 유지)

## 1. 개요
- Amazon Linux 2(AL2)는 2025년 6월 30일 EOL이며, AL2023으로의 마이그레이션이 필요합니다.
- AL2023은 AL2와 패키지 구조·커널·보안 설정이 달라 **In-Place 업그레이드가 공식 지원되지 않습니다** — 새 인스턴스를 기동해야 합니다.
- 새 인스턴스를 띄우면 Private/Public IP가 바뀌는 문제가 발생하므로, 운영 환경에서는 **IP를 보존하는 전략**이 필수입니다.

---

## 2. 설명

### 2.1 IP 보존이 필요한 이유

새 인스턴스 기동 시 변경되는 IP:
| IP 유형 | 변경 여부 | 영향 |
|---------|----------|------|
| Private IP | 변경됨 | 내부 서비스 연동, 온프레미스 방화벽 화이트리스트, DB 접근 제어 |
| Public IP (auto-assign) | 변경됨 | DNS A 레코드, 외부 방화벽 화이트리스트 |
| Elastic IP | 유지 가능 | EIP를 재연결하면 Public IP 유지 |

### 2.2 IP 유지 전략 비교

| 전략 | Private IP | Public IP | 다운타임 | 복잡도 |
|------|-----------|-----------|---------|--------|
| **[A] ENI(Elastic Network Interface) Swap** | 유지 | EIP 연동 시 유지 | 수십 초 | 중 |
| **[B] 동일 Private IP 지정 후 재기동** | 유지 | EIP 연동 시 유지 | 수 분 | 하 |
| **[C] NLB/ALB Target 교체** | 불필요 (클라이언트는 NLB IP 사용) | 불필요 | 거의 없음 | 하 |

---

### 2.3 전략별 상세 절차

---

#### [A] ENI Swap (Private IP 완전 유지, 권장)

ENI(Elastic Network Interface)는 인스턴스와 독립적으로 존재하므로, 같은 서브넷에서 다른 인스턴스로 이전할 수 있습니다.

**핵심 원리:**
```
[기존 AL2 인스턴스]          [신규 AL2023 인스턴스]
  eth0 ── eni-aaaa (Primary)    eth0 ── eni-cccc (임시 Primary, 나중에 제거)
  eth1 ── eni-bbbb (Secondary) ─────────────────────────→ attach eni-bbbb
           ↑ 이 ENI의 IP가 실제 서비스 IP
```

**사전 준비 — 서비스용 Secondary ENI 생성 및 기존 인스턴스에 연결:**
```bash
# 1. 서브넷에 Secondary ENI 생성 (원하는 Private IP 지정)
ENI_ID=$(aws ec2 create-network-interface \
  --subnet-id subnet-0abc1234 \
  --private-ip-address 10.0.1.100 \
  --groups sg-0service1234 \
  --description "service-floating-ip" \
  --query 'NetworkInterface.NetworkInterfaceId' \
  --output text)

echo "ENI: $ENI_ID"

# 2. 기존 AL2 인스턴스에 Secondary ENI 부착
aws ec2 attach-network-interface \
  --network-interface-id $ENI_ID \
  --instance-id i-0al2instance \
  --device-index 1

# 3. 애플리케이션을 Secondary IP(10.0.1.100)에 바인딩하도록 구성
# (예: Nginx listen 지시어, 앱 설정 파일 수정)
```

**마이그레이션 실행:**
```bash
OLD_INSTANCE_ID="i-0al2instance"
NEW_INSTANCE_ID="i-0al2023instance"   # AL2023으로 새로 기동한 인스턴스
ENI_ID="eni-0bbbb1234"

# 1. 기존 인스턴스에서 ENI 분리
ATTACHMENT_ID=$(aws ec2 describe-network-interfaces \
  --network-interface-ids $ENI_ID \
  --query 'NetworkInterfaces[0].Attachment.AttachmentId' \
  --output text)

aws ec2 detach-network-interface \
  --attachment-id $ATTACHMENT_ID \
  --force   # 인스턴스 running 중에도 강제 분리 가능

# 잠시 대기 (ENI 상태가 available로 전환)
aws ec2 wait network-interface-available --network-interface-ids $ENI_ID

# 2. 신규 AL2023 인스턴스에 ENI 부착
aws ec2 attach-network-interface \
  --network-interface-id $ENI_ID \
  --instance-id $NEW_INSTANCE_ID \
  --device-index 1

# 3. 신규 인스턴스 내에서 eth1 활성화
# (AL2023에서는 cloud-init이 자동 처리, 또는 수동으로)
# sudo ip link set eth1 up
# sudo dhclient eth1
```

**Terraform으로 ENI 사전 생성 및 관리:**
```hcl
resource "aws_network_interface" "service_ip" {
  subnet_id       = aws_subnet.private.id
  private_ips     = ["10.0.1.100"]
  security_groups = [aws_security_group.app.id]
  description     = "service-floating-ip — migration 후에도 유지"

  tags = {
    Name = "myapp-service-eni"
  }
  # attachment는 별도로 관리 (인스턴스 교체 시 재연결)
}

resource "aws_network_interface_attachment" "service_ip" {
  instance_id          = aws_instance.app.id
  network_interface_id = aws_network_interface.service_ip.id
  device_index         = 1
}
```

---

#### [B] 동일 Private IP 지정 후 재기동 (간단, 짧은 다운타임 허용 시)

같은 서브넷에서 특정 Private IP를 명시해 새 인스턴스를 기동하는 방법입니다.
단, 기존 인스턴스를 종료해야 해당 IP가 해제됩니다.

```bash
OLD_INSTANCE_ID="i-0al2instance"
PRIVATE_IP="10.0.1.50"
SUBNET_ID="subnet-0abc1234"
SG_ID="sg-0service1234"

# 1. 기존 인스턴스 중지 (stop: IP 보유, terminate: IP 반환)
#    ※ stop 상태에서는 IP가 유지되므로 terminate 해야 반환됨
aws ec2 stop-instances --instance-ids $OLD_INSTANCE_ID
aws ec2 wait instance-stopped --instance-ids $OLD_INSTANCE_ID

aws ec2 terminate-instances --instance-ids $OLD_INSTANCE_ID
aws ec2 wait instance-terminated --instance-ids $OLD_INSTANCE_ID
# ※ terminate 후 IP가 pool에 반환됨 — 다른 인스턴스가 선점할 위험 있음 (수 초 이내)

# 2. 동일 IP로 AL2023 인스턴스 즉시 기동
aws ec2 run-instances \
  --image-id ami-0al2023xxxxxxx \
  --instance-type m5.large \
  --subnet-id $SUBNET_ID \
  --security-group-ids $SG_ID \
  --private-ip-address $PRIVATE_IP \
  --iam-instance-profile Name=myapp-instance-profile \
  --launch-template LaunchTemplateName=myapp-lt,Version='$Latest' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=myapp-al2023}]'
```

> **주의:** terminate 직후 `run-instances`까지의 간격 동안 해당 IP가 다른 인스턴스에 할당될 수 있습니다. 격리된 전용 서브넷이나 IPAM 관리 환경이면 위험이 낮지만, 공유 서브넷에서는 ENI Swap 방식이 더 안전합니다.

---

#### [C] NLB/ALB Target 교체 (다운타임 최소화, 가장 권장)

클라이언트가 인스턴스 IP를 직접 사용하지 않고 NLB/ALB를 통해 접근한다면, 인스턴스 IP 변경은 문제가 되지 않습니다. 타겟만 교체하면 됩니다.

```bash
TG_ARN="arn:aws:elasticloadbalancing:ap-northeast-2:123456789012:targetgroup/myapp-tg/abc123"
OLD_INSTANCE_ID="i-0al2instance"
NEW_INSTANCE_ID="i-0al2023instance"
PORT=8080

# 1. 신규 AL2023 인스턴스를 타겟 그룹에 등록
aws elbv2 register-targets \
  --target-group-arn $TG_ARN \
  --targets Id=$NEW_INSTANCE_ID,Port=$PORT

# 2. 신규 인스턴스가 healthy 상태가 될 때까지 대기
aws elbv2 wait target-in-service \
  --target-group-arn $TG_ARN \
  --targets Id=$NEW_INSTANCE_ID,Port=$PORT

# 3. 기존 AL2 인스턴스 타겟 제거
aws elbv2 deregister-targets \
  --target-group-arn $TG_ARN \
  --targets Id=$OLD_INSTANCE_ID,Port=$PORT

echo "타겟 교체 완료. 기존 인스턴스 제거 가능."
```

Terraform으로 관리 중이라면 `aws_lb_target_group_attachment` 리소스의 `instance_id`만 교체하면 됩니다.

---

### 2.4 AL2023 인스턴스 사전 검증 체크리스트

마이그레이션 전, 새 AL2023 인스턴스에서 다음을 반드시 확인합니다:

```bash
# 1. 패키지 설치 확인 (yum → dnf)
dnf list installed | grep -E "amazon-cloudwatch-agent|codedeploy-agent|td-agent"

# 2. EPEL 의존 패키지 대체 확인
#    AL2023은 EPEL 미지원 — 대체 패키지 또는 소스 컴파일 필요

# 3. amazon-linux-extras 미지원 확인
amazon-linux-extras   # "command not found" 정상

# 4. SELinux 상태 확인
getenforce            # Permissive (기본값, Enforcing 전환 전 앱 테스트 필수)

# 5. SSH 키 타입 확인
ssh -Q key            # ecdsa, ed25519 지원 여부 (rsa SHA-1은 기본 차단)

# 6. UserData/cloud-init 로그 확인
cat /var/log/cloud-init-output.log | tail -50

# 7. systemd 서비스 상태
systemctl status myapp.service

# 8. 네트워크 인터페이스 확인 (ENI Swap 후)
ip addr show
ip route show
```

---

### 2.5 보안/비용 Best Practice

| 항목 | 권장 |
|------|------|
| **마이그레이션 순서** | 개발 → 스테이징 → 프로덕션 순으로 단계적 적용 |
| **ENI 태깅** | `Purpose=service-floating-ip` 태그 필수 — 실수로 삭제 방지 |
| **EIP 연동** | Public IP 유지가 필요하면 EIP를 ENI에 직접 연결 (인스턴스 독립) |
| **AMI 버전 고정** | Golden AMI를 사전 굽고, UserData 최소화 |
| **비용** | 마이그레이션 기간 중 이중 기동 비용 발생 — 검증 후 즉시 종료 |

---

## 3. 트러블슈팅

### 3.1 주요 이슈

#### [이슈 1] ENI Detach 후 eth1이 AL2023에서 UP 안 됨
- **증상:** ENI 부착 후 `ip addr` 에서 eth1이 `DOWN` 상태
- **원인:** AL2023의 NetworkManager가 secondary ENI를 자동 인식하지 못한 경우
- **해결:**
```bash
# 방법 1: nmcli로 수동 연결
sudo nmcli device status
sudo nmcli device connect eth1

# 방법 2: 직접 IP 설정
sudo ip link set eth1 up
sudo ip addr add 10.0.1.100/24 dev eth1
sudo ip route add default via 10.0.1.1 dev eth1 metric 200

# 방법 3: cloud-init에서 자동 처리 (권장)
# /etc/cloud/cloud.cfg.d/99-eni.cfg
# network:
#   version: 2
#   ethernets:
#     eth1:
#       dhcp4: true
```

#### [이슈 2] terminate 후 동일 IP 재사용이 안 됨
- **증상:** `run-instances --private-ip-address` 실행 시 "IP already in use" 오류
- **원인:** 이전 인스턴스가 완전히 terminate되지 않았거나, 다른 리소스(ENI)가 해당 IP를 점유 중
- **해결:**
```bash
# 해당 IP를 사용하는 ENI 확인
aws ec2 describe-network-interfaces \
  --filters "Name=addresses.private-ip-address,Values=10.0.1.50" \
  --query 'NetworkInterfaces[*].[NetworkInterfaceId,Status,Attachment.InstanceId]'
```

#### [이슈 3] EIP가 신규 인스턴스에 연결 안 됨
- **증상:** `associate-address` 명령이 성공하지만 외부에서 접속 불가
- **원인:** 인스턴스에 Public IP auto-assign이 비활성화되어 있고 EIP를 ENI가 아닌 인스턴스에 연결한 경우
- **해결:** EIP를 인스턴스가 아닌 **Primary ENI**에 직접 연결
```bash
# EIP를 ENI에 연결 (인스턴스 교체 후에도 유지)
PRIMARY_ENI=$(aws ec2 describe-instances \
  --instance-ids $NEW_INSTANCE_ID \
  --query 'Reservations[0].Instances[0].NetworkInterfaces[?Attachment.DeviceIndex==`0`].NetworkInterfaceId' \
  --output text)

aws ec2 associate-address \
  --allocation-id eipalloc-0abc1234 \
  --network-interface-id $PRIMARY_ENI
```

---

### 3.2 자주 발생하는 문제 (Q&A)

**Q: Stop 후 Start하면 Private IP가 바뀌나요?**
- A: **바뀌지 않습니다.** Stop/Start 사이클에서 Private IP는 유지됩니다. IP가 바뀌는 것은 `terminate` 후 새 인스턴스를 기동할 때입니다. 단, 인스턴스 스토어(ephemeral) 데이터는 Stop 시 소멸합니다.

**Q: ASG(Auto Scaling Group) 환경에서는 어떻게 하나요?**
- A: ASG는 Launch Template으로 새 인스턴스를 기동하므로 IP 고정이 어렵습니다. ALB/NLB를 통해 트래픽을 제어하는 [C] 방식을 사용하고, 내부 통신은 DNS(내부 Route 53 Private Hosted Zone 또는 Service Discovery)를 사용해 IP 의존성을 제거하는 것이 권장됩니다.

**Q: ENI Swap 중 트래픽 손실 시간은 얼마나 되나요?**
- A: Detach → Attach 과정에서 약 10~30초의 네트워크 중단이 발생합니다. 애플리케이션 레벨에서 재연결 로직(retry)이 있으면 실제 서비스 영향은 최소화됩니다. 더 짧은 중단이 필요하면 [C] NLB Target 교체 방식을 사용하세요.

**Q: RDS 보안 그룹이 특정 EC2 인스턴스 ID 기반으로 설정되어 있는데요?**
- A: RDS 보안 그룹 규칙은 인스턴스 ID가 아닌 **보안 그룹 ID**를 소스로 사용하는 것이 표준입니다. EC2와 동일한 보안 그룹을 신규 인스턴스에도 연결하면 문제없습니다. IP 기반 화이트리스트라면 ENI의 IP를 유지하는 것이 유일한 해결책입니다.

---

## 4. 모니터링 및 알람

```bash
# 마이그레이션 전후 주요 지표 비교 모니터링 쿼리 (CloudWatch Logs Insights)
# /var/log/messages 또는 앱 로그 기준

fields @timestamp, @message
| filter @message like /ERROR|WARN|Connection refused|timeout/
| stats count(*) as error_count by bin(1m)
| sort @timestamp desc
```

```hcl
# ENI 상태 변경 이벤트 알람 (EventBridge)
resource "aws_cloudwatch_event_rule" "eni_change" {
  name        = "eni-state-change"
  description = "서비스용 ENI 상태 변경 감지"

  event_pattern = jsonencode({
    source      = ["aws.ec2"]
    detail-type = ["EC2 Network Interface State-change Notification"]
    detail = {
      "network-interface-id" = ["eni-0bbbb1234"]
    }
  })
}
```

---

## 5. TIP

- **마이그레이션 스크립트 순서 정리 (ENI Swap 기준):**
  1. AL2023 AMI 기반 Golden AMI 사전 제작 (Packer/EC2 Image Builder)
  2. 스테이징 환경에서 앱 동작 검증 (패키지, UserData, SELinux)
  3. Secondary ENI를 서비스 IP로 사전 생성 및 AL2 인스턴스에 부착
  4. 앱을 Secondary IP에 바인딩하도록 재구성
  5. AL2023 인스턴스 기동 (임시 Primary ENI 사용)
  6. ENI Detach → Attach → 앱 기동 → 헬스체크
  7. 구 AL2 인스턴스 Terminate

- **ENI 보존 전략의 장점:** ENI 자체에 보안 그룹, EIP, Flow Logs 설정이 붙어 있으면 인스턴스 교체 시에도 그대로 유지됩니다.

- **AL2 EOL 이후에도 AL2 AMI는 계속 기동 가능합니다.** 보안 패치만 중단되므로, 마이그레이션 완료 전까지는 기존 인스턴스 운영은 가능하지만 보안 취약점 노출 위험을 감수해야 합니다.

- **관련 문서:**
  - [Amazon Linux 2023 공식 가이드](https://docs.aws.amazon.com/linux/al2023/ug/what-is-amazon-linux.html)
  - [EC2 ENI 관리 가이드](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-eni.html)
  - [`ec2-al2-al2023.md`](ec2-al2-al2023.md) — AL2 vs AL2023 차이점 비교
