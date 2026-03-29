# VPC & Subnet 설계 전략

## 1. 개요

VPC/Subnet CIDR 설계는 한번 결정하면 변경이 어렵고, 마이그레이션이나 서비스 확장 시 IP 부족으로 큰 문제가 된다.
처음부터 계정별 IP 공간을 충분히 확보하고, AZ별/용도별 subnet을 체계적으로 분리해야
EKS Prefix Delegation, 멀티 계정 피어링, 마이그레이션 시 Blue/Green 공존 환경을 원활히 운영할 수 있다.

---

## 2. 설명

### 2.1 핵심 개념

**IP 부족이 발생하는 주요 시나리오**

```
1. EKS 노드 증가: 노드당 최대 30~50개 Pod IP 필요 (ENI 한도)
2. Blue/Green 마이그레이션: 구버전 + 신버전 동시 운영 → 2배 IP 필요
3. RDS Multi-AZ + 읽기 복제본: AZ별로 개별 ENI 사용
4. VPC Endpoint: Interface Endpoint 1개당 AZ별 ENI 1개 소모
5. EKS Prefix Delegation: /28 블록 단위 할당 → 큰 블록 필요
```

**RFC 1918 사설 IP 공간 (멀티 계정 설계 시 전사 배분 기준)**

| 범위 | 크기 | 활용 권장 |
|------|------|---------|
| 10.0.0.0/8 | 16,777,216개 | 계정별 /16 단위로 배분 (최대 256개 계정) |
| 172.16.0.0/12 | 1,048,576개 | 개발/테스트 환경 |
| 192.168.0.0/16 | 65,536개 | 소규모 사내 네트워크만 |

**계정당 VPC 권장 CIDR 크기**

| 환경 | VPC CIDR | 이유 |
|------|---------|------|
| 프로덕션 | /16 (65,534개) | EKS 대규모 클러스터, 마이그레이션 여유 |
| 스테이징 | /18 (16,382개) | 프로덕션의 1/4 규모 |
| 개발 | /20 (4,094개) | 소규모이지만 /24보다 여유 있게 |

> **절대 /24 VPC로 시작하지 말 것**: 256개 IP에서 AWS 예약 5개 제외하면 251개뿐. EKS 노드 5개만 올려도 부족해짐.

**Subnet 3계층 설계 (Public / Private / Isolated)**

```
VPC: 10.10.0.0/16

Public Subnet (인터넷 접근 가능 — ALB, NAT GW)
  ap-northeast-2a: 10.10.0.0/24  (251개)
  ap-northeast-2b: 10.10.1.0/24  (251개)
  ap-northeast-2c: 10.10.2.0/24  (251개)

Private Subnet (NAT GW 통해 아웃바운드만 — EC2, EKS Node)
  ap-northeast-2a: 10.10.16.0/20  (4,091개)  ← 크게 잡을 것
  ap-northeast-2b: 10.10.32.0/20  (4,091개)
  ap-northeast-2c: 10.10.48.0/20  (4,091개)

Isolated Subnet (인터넷 차단 — RDS, ElastiCache, EKS Control Plane ENI)
  ap-northeast-2a: 10.10.64.0/24  (251개)
  ap-northeast-2b: 10.10.65.0/24  (251개)
  ap-northeast-2c: 10.10.66.0/24  (251개)

여유 공간: 10.10.128.0/17 (32,768개) ← Secondary CIDR 또는 미래 확장용
```

**EKS 전용 Subnet을 별도로 분리하는 이유**

```
EKS + Prefix Delegation(/28 단위):
  노드 1개당 /28 블록 2개 = 32개 IP
  노드 100개 = 3,200개 IP 소모

별도 EKS Subnet으로 분리하지 않으면:
  → RDS/EKS가 같은 subnet 쓰다가 IP 고갈
  → 추후 subnet 분리 불가 (기존 리소스 재배포 필요)
```

---

### 2.2 실무 적용 코드

**Terraform — VPC + 3계층 Subnet 생성**

```hcl
locals {
  azs = ["ap-northeast-2a", "ap-northeast-2b", "ap-northeast-2c"]

  # CIDR 계획
  public_cidrs   = ["10.10.0.0/24",  "10.10.1.0/24",  "10.10.2.0/24"]
  private_cidrs  = ["10.10.16.0/20", "10.10.32.0/20", "10.10.48.0/20"]
  isolated_cidrs = ["10.10.64.0/24", "10.10.65.0/24", "10.10.66.0/24"]
  eks_cidrs      = ["10.10.80.0/21", "10.10.88.0/21", "10.10.96.0/21"]   # EKS 전용 (2,048개씩)
}

resource "aws_vpc" "main" {
  cidr_block           = "10.10.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = { Name = "main-vpc" }
}

# Public Subnets
resource "aws_subnet" "public" {
  count             = length(local.azs)
  vpc_id            = aws_vpc.main.id
  cidr_block        = local.public_cidrs[count.index]
  availability_zone = local.azs[count.index]

  map_public_ip_on_launch = true   # Public subnet만 true

  tags = {
    Name = "public-${local.azs[count.index]}"
    # EKS ALB 자동 발견을 위한 태그
    "kubernetes.io/role/elb"             = "1"
    "kubernetes.io/cluster/my-cluster"   = "owned"
  }
}

# Private Subnets (EC2, 일반 워크로드)
resource "aws_subnet" "private" {
  count             = length(local.azs)
  vpc_id            = aws_vpc.main.id
  cidr_block        = local.private_cidrs[count.index]
  availability_zone = local.azs[count.index]

  tags = {
    Name = "private-${local.azs[count.index]}"
  }
}

# EKS 전용 Private Subnets
resource "aws_subnet" "eks" {
  count             = length(local.azs)
  vpc_id            = aws_vpc.main.id
  cidr_block        = local.eks_cidrs[count.index]
  availability_zone = local.azs[count.index]

  tags = {
    Name = "eks-${local.azs[count.index]}"
    # EKS 내부 LB 태그
    "kubernetes.io/role/internal-elb"    = "1"
    "kubernetes.io/cluster/my-cluster"   = "owned"
    # Karpenter가 subnet을 자동 발견하는 태그
    "karpenter.sh/discovery"             = "my-cluster"
  }
}

# Isolated Subnets (RDS, ElastiCache)
resource "aws_subnet" "isolated" {
  count             = length(local.azs)
  vpc_id            = aws_vpc.main.id
  cidr_block        = local.isolated_cidrs[count.index]
  availability_zone = local.azs[count.index]

  tags = { Name = "isolated-${local.azs[count.index]}" }
}

# Internet Gateway
resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "main-igw" }
}

# NAT Gateway (AZ당 1개 — HA, 비용 절충은 아래 참고)
resource "aws_eip" "nat" {
  count  = length(local.azs)
  domain = "vpc"
}

resource "aws_nat_gateway" "main" {
  count         = length(local.azs)
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id

  tags = { Name = "nat-${local.azs[count.index]}" }
}

# Route Tables
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "rt-public" }
}

resource "aws_route_table" "private" {
  count  = length(local.azs)
  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main[count.index].id   # AZ별 로컬 NAT GW
  }

  tags = { Name = "rt-private-${local.azs[count.index]}" }
}

resource "aws_route_table" "isolated" {
  vpc_id = aws_vpc.main.id
  # 라우트 없음 — 인터넷 차단
  tags = { Name = "rt-isolated" }
}

# Route Table 연결
resource "aws_route_table_association" "public" {
  count          = length(local.azs)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = length(local.azs)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

resource "aws_route_table_association" "eks" {
  count          = length(local.azs)
  subnet_id      = aws_subnet.eks[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

resource "aws_route_table_association" "isolated" {
  count          = length(local.azs)
  subnet_id      = aws_subnet.isolated[count.index].id
  route_table_id = aws_route_table.isolated.id
}
```

**마이그레이션 시 Secondary CIDR 추가 (IP 부족 해결)**

```hcl
# 기존 VPC에 Secondary CIDR 추가 (서비스 중단 없음)
resource "aws_vpc_ipv4_cidr_block_association" "secondary" {
  vpc_id     = aws_vpc.main.id
  cidr_block = "10.20.0.0/16"   # 추가 IP 공간 확보
}

# Secondary CIDR로 새 EKS 전용 Subnet 생성
resource "aws_subnet" "eks_v2" {
  count             = length(local.azs)
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet("10.20.0.0/16", 4, count.index)
  availability_zone = local.azs[count.index]

  depends_on = [aws_vpc_ipv4_cidr_block_association.secondary]

  tags = { Name = "eks-v2-${local.azs[count.index]}" }
}
```

**사용 가능한 IP 수 빠른 계산**

```bash
# cidrhost 수 = 2^(32-prefix) - 5 (AWS 예약 5개)
# /16 = 65536 - 5 = 65531
# /18 = 16384 - 5 = 16379
# /20 = 4096  - 5 = 4091
# /21 = 2048  - 5 = 2043
# /22 = 1024  - 5 = 1019
# /24 = 256   - 5 = 251  ← EKS subnet으로는 너무 작음

# 현재 subnet 사용 현황 확인
aws ec2 describe-subnets \
  --filters "Name=vpc-id,Values=vpc-xxxxxxxx" \
  --query 'Subnets[*].{ID:SubnetId,AZ:AvailabilityZone,CIDR:CidrBlock,Available:AvailableIpAddressCount}' \
  --output table
```

---

### 2.3 보안/비용 Best Practice

- **NAT GW AZ별 1개 vs 1개 공유**: AZ당 1개면 비용 증가($0.059/h × 3AZ) 대신 AZ 장애 시에도 아웃바운드 유지. 비용 절감이 중요하면 1개만 두되 HA 포기
- **Isolated Subnet에 NAT GW 라우트 없이**: RDS/ElastiCache는 인터넷 불필요. 라우트 없는 독립 라우팅 테이블 사용
- **VPC CIDR은 다른 계정과 겹치지 않게**: Transit Gateway나 VPC Peering 사용 시 CIDR 충돌하면 라우팅 불가. IP 관리 스프레드시트 필수
- **Secondary CIDR의 한계**: 1개 VPC에 최대 5개 CIDR 추가 가능. 처음부터 넉넉히 설계하는 게 최선

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**마이그레이션 중 IP 고갈**

```bash
# 증상: EKS 노드 추가 시 "not enough free addresses in subnet" 오류
# 원인: subnet /24 → 251개 IP, EKS Prefix Delegation으로 빠르게 고갈

# 1단계: 현재 IP 사용 현황 확인
aws ec2 describe-network-interfaces \
  --filters "Name=subnet-id,Values=subnet-xxxxxxxx" \
  --query 'NetworkInterfaces[*].{IP:PrivateIpAddress,Description:Description}' \
  --output table

# 2단계: Secondary CIDR 추가
aws ec2 associate-vpc-cidr-block \
  --vpc-id vpc-xxxxxxxx \
  --cidr-block 10.20.0.0/16

# 3단계: 새 subnet 생성 후 EKS NodeGroup을 새 subnet으로 마이그레이션
```

**EKS 노드가 특정 AZ에만 스케줄링됨**

```bash
# 원인: 특정 AZ subnet의 IP가 고갈되어 다른 AZ로만 노드 추가됨
# 결과: 모든 Pod가 한 AZ에 몰려 단일 장애점 발생

# AZ별 IP 잔여량 비교
aws ec2 describe-subnets \
  --filters "Name=tag:kubernetes.io/cluster/my-cluster,Values=owned" \
  --query 'sort_by(Subnets, &AvailableIpAddressCount)[*].{AZ:AvailabilityZone,Available:AvailableIpAddressCount,CIDR:CidrBlock}'
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: AWS가 subnet에서 예약하는 IP 5개는 어떤 건가요?**
A: 첫 번째(.0 네트워크 주소), 두 번째(.1 VPC 라우터), 세 번째(.2 DNS), 네 번째(.3 AWS 예약), 마지막(.255 브로드캐스트). 10.10.0.0/24라면 10.10.0.0, 10.10.0.1, 10.10.0.2, 10.10.0.3, 10.10.0.255가 예약됨.

**Q: Subnet을 나중에 확장(CIDR 변경)할 수 있나요?**
A: 불가능합니다. Subnet CIDR은 생성 후 변경이 안 됩니다. 해결책은 Secondary CIDR로 VPC에 IP를 추가하고, 새 subnet을 만든 후 리소스를 이전하는 것입니다.

**Q: 3개 AZ가 아닌 2개 AZ만 써도 되나요?**
A: 가능하지만 권장하지 않습니다. ap-northeast-2의 경우 AZ가 4개(a/b/c/d)이며, AWS가 내부적으로 물리 데이터센터를 매핑하는 방식이 계정마다 다릅니다. 최소 3개 AZ를 사용하세요.

---

## 4. 모니터링 및 알람

```hcl
# Subnet IP 잔여량 부족 알람
resource "aws_cloudwatch_metric_alarm" "subnet_ip_low" {
  for_each = toset(aws_subnet.eks[*].id)

  alarm_name          = "subnet-ip-low-${each.value}"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  metric_name         = "AvailableIpAddressCount"
  namespace           = "AWS/EC2"
  period              = 300
  statistic           = "Minimum"
  threshold           = 50   # 잔여 IP 50개 미만 시 알람

  dimensions = {
    SubnetId = each.value
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
}
```

---

## 5. TIP

- **IP 주소 계획 스프레드시트**: 멀티 계정 환경에서는 계정별 /16 CIDR을 미리 할당표에 기록. 나중에 Transit Gateway 연결 시 CIDR 충돌 방지
- **EKS Prefix Delegation 활성화 시 subnet 크기**: /21 이상(2,048개) 권장. /24로는 노드 10개도 못 채울 수 있음 (`eks-networking-vpc-cni.md` 참고)
- **IPv6 듀얼스택 고려**: IPv4 고갈 대비로 VPC에 IPv6 /56 할당 가능. EKS는 IPv6 전용 모드도 지원 (Pod에 IPv6만 할당하면 IP 고갈 문제 근본 해결)
- **Terraform CIDR 헬퍼 함수**: `cidrsubnet("10.10.0.0/16", 4, 0)` → 10.10.0.0/20 자동 계산으로 오타 방지
