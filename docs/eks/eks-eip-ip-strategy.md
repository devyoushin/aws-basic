# EKS Node EIP 할당 한도와 IP 전략

## 1. 개요

EKS 노드(EC2 인스턴스)에 Elastic IP(EIP)를 할당할 때는 **인스턴스 타입별 ENI/IP 한도**,
**AWS 계정 EIP 쿼터**, **아웃바운드 트래픽 전략(NAT vs EIP)** 세 가지를 함께 고려해야 한다.
대규모 클러스터에서 EIP를 잘못 설계하면 IP 고갈, 비용 급증, 보안 취약점이 동시에 발생한다.

**핵심 요약**
- **노드당 EIP 수**: 이론상 ENI×ENI당IP 개수만큼 가능하지만, 실질적으로는 노드 1개당 EIP 1개
- **계정 EIP 쿼터**: 기본 5개/리전 (Service Quotas로 증가 가능)
- **권장 전략**: 프로덕션은 Private Subnet + NAT Gateway, 인터넷 직접 노출이 필요하면 NLB 사용

---

## 2. 설명

### 2.1 EIP 할당 한도 계층 구조

```
AWS 계정 레벨
└── EIP 쿼터: 기본 5개/리전 (증가 신청 가능, 실무 100~200개 요청)
        │
        ▼
EC2 인스턴스 레벨
└── ENI 수 × ENI당 Private IP 수 = 연결 가능한 EIP 상한
    (단, EIP는 Private IP 1개에 1:1 매핑)
        │
        ▼
실질 운영 상한
└── 노드 1개당 EIP 1개 (기본 ENI의 Primary IP에 연결)
```

**인스턴스 타입별 ENI 및 IP 한도**

| 인스턴스 타입 | 최대 ENI | ENI당 최대 IP | 이론적 최대 EIP | 실운영 EIP |
|-------------|---------|-------------|--------------|-----------|
| t3.medium | 3 | 6 | 18 | 1 |
| m5.large | 3 | 10 | 30 | 1 |
| m5.xlarge | 4 | 15 | 60 | 1 |
| m5.4xlarge | 8 | 30 | 240 | 1 |
| c5.18xlarge | 15 | 50 | 750 | 1 |

> **핵심**: 이론 수치는 의미 없음. EKS에서 Secondary ENI/IP는 Pod IP로 사용되므로
> 노드에 EIP를 대량 연결하는 구조는 운영하지 않는다.

**EIP 쿼터 확인 및 증가 요청**

```bash
# 현재 EIP 사용 현황 확인
aws ec2 describe-addresses \
  --query 'Addresses[*].[AllocationId,PublicIp,InstanceId,AssociationId]' \
  --output table \
  --region ap-northeast-2

# 현재 EIP 쿼터 확인
aws service-quotas get-service-quota \
  --service-code ec2 \
  --quota-code L-0263D0A3 \
  --region ap-northeast-2
# QuotaName: EC2-VPC Elastic IPs, Value: 5.0 (기본)

# 쿼터 증가 요청
aws service-quotas request-service-quota-increase \
  --service-code ec2 \
  --quota-code L-0263D0A3 \
  --desired-value 100 \
  --region ap-northeast-2
```

---

### 2.2 EKS IP 전략 패턴 4가지

#### 패턴 A — Private Subnet + NAT Gateway (프로덕션 권장)

```
인터넷
  │
[Internet Gateway]
  │
[NAT Gateway] ← EIP 1개 (AZ당)
  │
[Private Subnet] — EKS 노드/Pod
  │                   아웃바운드: NAT Gateway 경유
  └── EIP 없음 (노드/Pod 모두 Private IP)
```

```hcl
# Terraform — NAT Gateway + Private Subnet EKS
resource "aws_eip" "nat" {
  count  = length(var.availability_zones)  # AZ 수만큼 EIP
  domain = "vpc"

  tags = {
    Name = "eks-nat-eip-${var.availability_zones[count.index]}"
  }
}

resource "aws_nat_gateway" "main" {
  count         = length(var.availability_zones)
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id

  tags = {
    Name = "eks-nat-${var.availability_zones[count.index]}"
  }
}

resource "aws_route_table" "private" {
  count  = length(var.availability_zones)
  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main[count.index].id
  }
}
```

**장점**: 보안, 비용 예측 가능 (EIP AZ당 1개)
**단점**: NAT Gateway 비용 (약 $0.045/시간 + 데이터 처리 비용)

---

#### 패턴 B — Public Subnet + 노드 EIP (소규모/개발 환경)

```
인터넷
  │
[Internet Gateway]
  │
[Public Subnet] — EKS 노드 (각 노드에 EIP 직접 연결)
  │               노드 EIP = 외부 접근 가능
  └── EIP: 노드 수만큼 필요 (노드 1개 = EIP 1개)
```

```bash
# 노드 시작 시 EIP 자동 연결 (Launch Template UserData)
#!/bin/bash
INSTANCE_ID=$(curl -s http://169.254.169.254/latest/meta-data/instance-id)
ALLOCATION_ID="eipalloc-xxxxxxxxxxxxxxxxx"  # 미리 할당된 EIP

aws ec2 associate-address \
  --instance-id $INSTANCE_ID \
  --allocation-id $ALLOCATION_ID \
  --region ap-northeast-2
```

```bash
# EIP 사전 할당 (노드 수만큼)
for i in $(seq 1 5); do
  aws ec2 allocate-address \
    --domain vpc \
    --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=Name,Value=eks-node-eip-$i}]" \
    --region ap-northeast-2
done
```

**장점**: 단순, NAT Gateway 비용 없음
**단점**: 보안 취약 (노드 직접 노출), EIP 수동 관리, ASG 스케일아웃 시 EIP 부족 가능

---

#### 패턴 C — 특정 Pod에만 EIP 연결 (Security Groups for Pods + EIP)

```
[Pod A] — 전용 ENI — EIP (고정 아웃바운드 IP 필요한 워크로드)
[Pod B] — 공유 ENI — NAT Gateway 경유 (일반 워크로드)
```

EKS에서 특정 Pod에 고정 EIP가 필요한 경우 (예: 외부 방화벽 허용 목록):
- VPC CNI의 Security Groups for Pods 기능 활용
- 전용 ENI를 Pod에 할당하고 해당 ENI에 EIP 연결

```bash
# Pod 전용 ENI 생성 및 EIP 연결
ENI_ID=$(aws ec2 create-network-interface \
  --subnet-id subnet-xxxxxxxx \
  --groups sg-xxxxxxxx \
  --description "Pod dedicated ENI" \
  --query 'NetworkInterface.NetworkInterfaceId' \
  --output text \
  --region ap-northeast-2)

EIP_ALLOC=$(aws ec2 allocate-address \
  --domain vpc \
  --query 'AllocationId' \
  --output text \
  --region ap-northeast-2)

aws ec2 associate-address \
  --allocation-id $EIP_ALLOC \
  --network-interface-id $ENI_ID \
  --region ap-northeast-2
```

**장점**: 특정 워크로드만 고정 IP, 나머지는 NAT 경유
**단점**: 운영 복잡도 높음, Nitro 인스턴스 필요

---

#### 패턴 D — IPv6 Dual-Stack (차세대 전략)

```
[EKS 노드] IPv4(Private) + IPv6(공개 주소)
[Pod]       IPv6 직접 통신 (NAT 불필요)
            IPv4 → IPv4 통신은 NAT64 경유
```

```hcl
# VPC IPv6 활성화
resource "aws_vpc" "main" {
  cidr_block                       = "10.0.0.0/16"
  assign_generated_ipv6_cidr_block = true

  tags = {
    Name = "eks-ipv6-vpc"
  }
}

resource "aws_subnet" "private" {
  vpc_id                          = aws_vpc.main.id
  cidr_block                      = "10.0.1.0/24"
  ipv6_cidr_block                 = cidrsubnet(aws_vpc.main.ipv6_cidr_block, 8, 1)
  assign_ipv6_address_on_creation = true
}
```

**장점**: EIP 불필요, IP 고갈 없음, 직접 라우팅
**단점**: IPv6 미지원 서비스와의 연동 복잡, 기존 레거시 환경과의 호환성

---

### 2.3 전략 선택 가이드

| 환경 | 권장 패턴 | 이유 |
|------|----------|------|
| 프로덕션 (보안 중요) | A — Private + NAT | 최소 노출, 안정적 |
| 개발/테스트 소규모 | B — Public + EIP | 단순, 비용 절감 |
| 고정 IP 필요 워크로드 | A + C 혼합 | 일부만 EIP, 나머지 NAT |
| 신규 대규모 클러스터 | D — IPv6 | IP 확장성, 미래 지향 |
| 멀티 클러스터/온프레미스 연동 | A + Transit Gateway | 중앙화된 라우팅 |

**아웃바운드 EIP 소비 계산 예시**

```
시나리오: 3 AZ, 노드 그룹당 최대 10개 노드
패턴 A (NAT Gateway):
  EIP 필요: 3개 (AZ당 NAT 1개)
  비용: NAT Gateway $0.045/시간 × 3 = $97.2/월 (트래픽 비용 별도)

패턴 B (노드 EIP):
  EIP 필요: 최대 30개 (노드 수만큼)
  비용: EIP 연결 중 무료, 미연결 EIP $0.005/시간
  → 쿼터 초과 가능 (기본 5개), 증가 신청 필요
```

---

### 2.4 보안/비용 Best Practice

**보안**
- 프로덕션 EKS 노드는 **반드시 Private Subnet** — 인터넷에서 노드 직접 접근 차단
- 외부 인바운드 트래픽은 **NLB/ALB를 통해서만 수신** (노드 EIP로 직접 수신 금지)
- EIP를 사용한다면 **Security Group 최소 허용 원칙** 적용

**비용**
- AZ당 NAT Gateway 1개 권장 (AZ 간 데이터 전송 비용 $0.01/GB 절감)
- 미사용 EIP는 즉시 해제 ($0.005/시간 과금)
- 대용량 아웃바운드 트래픽은 NAT Gateway 대신 VPC Endpoint 우선 검토

---

## 3. 트러블슈팅

### 3.1 주요 이슈

#### EIP 할당 실패 — AddressLimitExceeded

**증상**
- ASG 스케일아웃 시 새 노드가 EIP를 할당받지 못하고 UserData 실패

**원인**
- 계정 EIP 쿼터(기본 5개) 초과

**해결 방법**
```bash
# 현재 EIP 사용 수 확인
aws ec2 describe-addresses \
  --query 'length(Addresses)' \
  --region ap-northeast-2

# 미사용 EIP 해제
aws ec2 describe-addresses \
  --query 'Addresses[?AssociationId==null].AllocationId' \
  --output text \
  --region ap-northeast-2 | \
xargs -I {} aws ec2 release-address \
  --allocation-id {} \
  --region ap-northeast-2

# 쿼터 증가 요청 (처리 시간 1~3 영업일)
aws service-quotas request-service-quota-increase \
  --service-code ec2 \
  --quota-code L-0263D0A3 \
  --desired-value 50 \
  --region ap-northeast-2
```

#### 노드 교체 후 EIP 유실

**증상**
- ASG가 노드를 교체하면서 기존 노드의 EIP 연결이 끊어짐

**원인**
- EIP가 인스턴스에 연결됐다가 인스턴스 종료 시 자동 해제됨

**해결 방법**
```bash
# ENI에 EIP 연결 (인스턴스 교체에도 유지)
# ENI가 유지되면 EIP도 유지됨
# → Lifecycle Hook에서 ENI를 새 인스턴스에 이동

aws ec2 associate-address \
  --network-interface-id eni-xxxxxxxx \
  --allocation-id eipalloc-xxxxxxxx \
  --allow-reassociation \
  --region ap-northeast-2
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: Pod에서 아웃바운드 IP를 고정하고 싶으면 어떻게 하나요?**
A: 3가지 방법이 있습니다.
1) 노드 EIP — 노드의 모든 Pod가 동일한 EIP 사용 (패턴 B)
2) Security Groups for Pods + ENI EIP — 특정 Pod만 전용 EIP (패턴 C)
3) NAT Gateway의 EIP — 모든 Private 서브넷 트래픽이 NAT EIP 사용 (패턴 A)
운영 단순성 기준으로 NAT Gateway EIP → 노드 EIP → Pod ENI EIP 순서로 선택하세요.

**Q: EKS Fargate에서 고정 아웃바운드 IP가 필요하면?**
A: Fargate Pod는 ENI를 직접 갖지만 EIP 연결이 불가능합니다. Fargate는 반드시
Private Subnet + NAT Gateway 구성을 사용해야 하며, 아웃바운드 IP = NAT Gateway EIP가 됩니다.

---

## 4. 모니터링 및 알람

### CloudWatch 핵심 지표

| 지표 | 네임스페이스 | 의미 | 임계값 예시 |
|------|-------------|------|------------|
| `BytesOutToDestination` | `AWS/NatGateway` | NAT Gateway 아웃바운드 트래픽 | 이상 급증 모니터링 |
| `ErrorPortAllocation` | `AWS/NatGateway` | NAT Gateway 포트 소진 | `> 0` |
| 서비스 쿼터 알람 | `AWS/Usage` | EIP 쿼터 사용률 | `> 80%` |

```bash
# NAT Gateway 포트 소진 알람
aws cloudwatch put-metric-alarm \
  --alarm-name "nat-gw-port-allocation-error" \
  --alarm-description "NAT Gateway 포트 소진 — SNAT 포트 부족" \
  --metric-name "ErrorPortAllocation" \
  --namespace "AWS/NatGateway" \
  --dimensions Name=NatGatewayId,Value=nat-xxxxxxxxxxxxxxxxx \
  --statistic Sum \
  --period 60 \
  --evaluation-periods 1 \
  --threshold 0 \
  --comparison-operator GreaterThanThreshold \
  --alarm-actions "arn:aws:sns:ap-northeast-2:123456789012:<SNS_TOPIC>" \
  --region ap-northeast-2
```

---

## 5. TIP

- **NAT Gateway SNAT 포트 소진**: 동일 NAT를 통해 동일 외부 IP:Port에 과도한 연결이 몰리면 `ErrorPortAllocation` 발생 — AZ별 NAT 분산 + 연결 재사용(Keep-Alive) 설정으로 완화
- **EIP vs NAT 비용 분기점**: 아웃바운드 데이터가 월 1TB 미만이면 NAT Gateway 비용이 EIP 직접 연결보다 경제적 (데이터 처리 비용 $0.045/GB)
- **IPv6 전환 시 EIP 제로화 가능**: IPv6 주소는 기본적으로 공인 주소이므로 EIP 불필요 — 신규 클러스터는 IPv6 Dual-Stack 설계 권장
- **EIP 태그 관리**: `Environment`, `Cluster`, `AZ` 태그를 달아두면 미사용 EIP 정리 자동화 스크립트 작성 시 활용 가능

**관련 문서**
- 연관 내부 문서: `docs/eks/eks-networking-vpc-cni.md`, `docs/network/vpc-subnet-design.md`, `docs/network/nlb-ec2-port-forwarding.md`
