# EC2 Enhanced Networking & 네트워크 대역폭

## 1. 개요

Enhanced Networking은 SR-IOV (Single Root I/O Virtualization) 기술을 사용하여
하이퍼바이저 우회로 저지연, 고처리량, 낮은 지터를 제공하는 EC2 네트워크 기능이다.
현세대 인스턴스(C5, M5, R5 등)는 ENA (Elastic Network Adapter)를 통해 기본 활성화되어 있으며,
인스턴스 타입마다 네트워크 대역폭 한계가 다르므로 워크로드에 맞는 선택이 중요하다.

---

## 2. 설명

### 2.1 핵심 개념

**Enhanced Networking 동작 원리**

```
일반 가상화:
  EC2 인스턴스 → 하이퍼바이저 (소프트웨어 처리) → 물리 NIC
  단점: 하이퍼바이저 오버헤드, 높은 CPU 사용률, 높은 지연

Enhanced Networking (SR-IOV):
  EC2 인스턴스 → Virtual Function (직접 NIC 접근) → 물리 NIC
  장점: 하이퍼바이저 우회, 낮은 지연(수십 μs), 높은 처리량, 낮은 CPU 사용률
```

**ENA vs Intel VF 비교**

| 항목 | ENA (Elastic Network Adapter) | Intel 82599 VF |
|------|-------------------------------|----------------|
| 최대 대역폭 | 100 Gbps | 10 Gbps |
| 지원 인스턴스 | 현세대 대부분 | 일부 구세대 |
| Enhanced Networking | Intel VF보다 우수 | 기본 |

**인스턴스 타입별 네트워크 대역폭 (ap-northeast-2)**

| 인스턴스 타입 | 네트워크 대역폭 | EBS 대역폭 |
|-------------|--------------|-----------|
| t3.medium | 최대 5 Gbps | 최대 2,085 Mbps |
| m5.xlarge | 최대 10 Gbps | 최대 4,750 Mbps |
| m5.4xlarge | 최대 10 Gbps | 최대 4,750 Mbps |
| m5.12xlarge | 12 Gbps | 9,500 Mbps |
| m5.24xlarge | 25 Gbps | 19,000 Mbps |
| c5n.9xlarge | 50 Gbps | 19,000 Mbps |
| c5n.18xlarge | 100 Gbps | 19,000 Mbps |

주의: "최대 X Gbps"로 표시된 항목은 Burst 대역폭 (지속 불가). 기준 대역폭은 더 낮음.

**Baseline vs Burst 네트워크 대역폭**
- 소규모 인스턴스(t3, m5.large 등)는 Burst 허용
- 버스트 크레딧 소진 시 Baseline 대역폭으로 하락
- 대규모 인스턴스(m5.12xlarge 이상)는 전용 대역폭 (항상 보장)

---

### 2.2 실무 적용 코드

**ENA 활성화 확인**

```bash
# OS에서 ENA 드라이버 확인
ethtool -i eth0 | grep driver
# driver: ena   ← ENA 활성화됨
# driver: ixgbevf  ← Intel VF (구형)

# ENA 상세 정보
ethtool -g eth0  # 링 버퍼 크기

# 현재 네트워크 처리량 확인 (1초 간격)
sar -n DEV 1 10
```

**iperf3 — 네트워크 성능 측정**

```bash
# 같은 VPC 내 두 인스턴스에서 테스트
# 서버 (인스턴스 A)
iperf3 -s -p 5201

# 클라이언트 (인스턴스 B)
# TCP 단일 스트림
iperf3 -c <server-ip> -p 5201 -t 30

# TCP 병렬 스트림 (대역폭 최대치 측정)
iperf3 -c <server-ip> -p 5201 -t 30 -P 16

# UDP 지연시간 측정
iperf3 -c <server-ip> -u -b 1G -t 30

# 예상 결과 (같은 Cluster 배치 그룹, c5n.18xlarge)
# [ 5] 0.00-30.00 sec  350 GBytes  100 Gbits/sec
```

**네트워크 대역폭 CloudWatch 모니터링**

```bash
# 인스턴스 수준 네트워크 지표 (1분 평균)
aws cloudwatch get-metric-statistics \
  --namespace AWS/EC2 \
  --metric-name NetworkOut \
  --dimensions Name=InstanceId,Value=i-xxxxxxxx \
  --start-time $(date -u -v-1H +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 60 \
  --statistics Average \
  --query 'Datapoints[*].[Timestamp,Average]' \
  --output table
```

**멀티 ENI 구성 — 트래픽 분리**

```hcl
resource "aws_instance" "nat" {
  ami           = data.aws_ami.al2023.id
  instance_type = "c5.xlarge"

  # 첫 번째 ENI (공개 서브넷)
  network_interface {
    network_interface_id = aws_network_interface.public.id
    device_index         = 0
  }

  # 두 번째 ENI (프라이빗 서브넷 — 내부 트래픽 분리)
  network_interface {
    network_interface_id = aws_network_interface.private.id
    device_index         = 1
  }
}
```

**OS 레벨 네트워크 최적화**

```bash
# TCP 수신 버퍼 크기 최적화 (고처리량 환경)
echo "net.core.rmem_max = 134217728" | sudo tee -a /etc/sysctl.conf
echo "net.core.wmem_max = 134217728" | sudo tee -a /etc/sysctl.conf
echo "net.ipv4.tcp_rmem = 4096 65536 134217728" | sudo tee -a /etc/sysctl.conf
echo "net.ipv4.tcp_wmem = 4096 65536 134217728" | sudo tee -a /etc/sysctl.conf
sudo sysctl -p

# IRQ Affinity 설정 (NIC 인터럽트를 여러 CPU에 분산)
# Amazon Linux에서는 irqbalance가 자동 처리
sudo systemctl status irqbalance
```

---

### 2.3 보안/비용 Best Practice

- **네트워크 집약적 워크로드**: c5n, m5n 같은 `n` 접미사 인스턴스 선택 (네트워크 최적화)
- **같은 AZ 내 통신**: AZ 간 데이터 전송 비용 ($0.01/GB) 발생 → 가능하면 같은 AZ 배치
- **VPC Endpoint 활용**: S3, DynamoDB 등 AWS 서비스는 VPC Endpoint로 인터넷 없이 접근 (대역폭 절감)
- **EFA는 Cluster 배치 그룹 필수**: Placement Group 없이는 EFA 최대 성능 불가

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**네트워크 성능이 기대치 미달**

```bash
# 1. 현재 인스턴스 네트워크 대역폭 한계 확인
aws ec2 describe-instance-types \
  --instance-types m5.xlarge \
  --query 'InstanceTypes[*].NetworkInfo'
# "NetworkPerformance": "Up to 10 Gigabit"
# "BaselineBandwidthInGbps": 0.75  ← 실제 기준 대역폭은 750 Mbps!

# 2. ENA 드라이버 확인
ethtool -i eth0

# 3. CPU 병목 확인 (네트워크 처리에 CPU 사용되는지)
top -d 1
# si: Software IRQ → 높으면 네트워크 인터럽트 처리로 CPU 소비 중

# 4. 패킷 드롭 확인
ethtool -S eth0 | grep -i drop
ip -s link show eth0
```

**ENI 추가 후 라우팅 문제**

```bash
# 인스턴스에 ENI 추가 시 두 번째 ENI에 대한 라우팅 설정 필요
# (Amazon Linux 2023은 자동 처리되는 경우 많음)

# eth1 라우팅 테이블 확인
ip route show table main
ip route show table 101  # ENI별 라우팅 테이블

# 수동 라우팅 설정 (필요 시)
sudo ip route add default via 10.0.2.1 dev eth1 table 101
sudo ip rule add from 10.0.2.x/24 lookup 101
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: t3.medium의 네트워크가 느린 이유가 뭔가요?**
A: t3.medium은 "Up to 5 Gbps"이지만 Burst 대역폭입니다. 기준(Baseline) 대역폭은 약 0.256 Gbps입니다. 지속적인 고처리량이 필요하면 전용 대역폭을 가진 m5.large (최대 10 Gbps, Baseline 0.75 Gbps) 이상을 사용하세요.

**Q: 같은 VPC 내 인스턴스 간 대역폭이 공식 스펙보다 낮게 측정됩니다**
A: 단일 TCP 흐름(flow)은 한 쌍의 IP:Port에 묶여 단일 CPU 코어에서 처리됩니다. 최대 대역폭을 활용하려면 `iperf3 -P 16`처럼 다중 병렬 스트림으로 테스트하세요.

---

## 4. 모니터링 및 알람

```hcl
resource "aws_cloudwatch_metric_alarm" "network_out_high" {
  alarm_name          = "network-out-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "NetworkOut"
  namespace           = "AWS/EC2"
  period              = 60
  statistic           = "Average"
  # m5.xlarge 기준 대역폭 750 Mbps ≈ 93.75 MB/s → 바이트 단위
  threshold           = 90000000   # ~90 MB/s (대역폭 포화 임박)

  dimensions = {
    InstanceId = aws_instance.app.id
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
}
```

**핵심 CloudWatch 지표**

| 지표 | 설명 |
|------|------|
| `NetworkIn` / `NetworkOut` | 인스턴스 전체 네트워크 트래픽 (Bytes) |
| `NetworkPacketsIn` / `NetworkPacketsOut` | 패킷 수 |

---

## 5. TIP

- **인스턴스 타입별 Baseline 대역폭 확인**: AWS 공식 문서의 "Amazon EC2 instance network bandwidth" 페이지 참고 — "Up to X Gbps"는 Burst이고 실제 안정적으로 사용 가능한 Baseline은 더 낮음
- **EFA for MPI**: MPI (Message Passing Interface) 기반 HPC 워크로드에서 EFA는 InfiniBand 수준의 성능 제공
- **Jumbo Frames (MTU 9001)**: VPC 내부 통신에서 MTU를 9001로 설정하면 대용량 데이터 전송 효율 향상 (EC2 인스턴스 간, 동일 VPC 내)
  ```bash
  sudo ip link set dev eth0 mtu 9001
  ```
