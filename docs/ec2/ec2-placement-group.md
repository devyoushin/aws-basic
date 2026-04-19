# EC2 배치 그룹 (Placement Group)

## 1. 개요

배치 그룹은 EC2 인스턴스의 물리적 배치를 제어하는 기능이다.
워크로드 특성에 따라 세 가지 전략 중 선택하며,
잘못 선택하면 성능 저하나 가용성 감소로 이어질 수 있다.

---

## 2. 설명

### 2.1 핵심 개념

**배치 그룹 3가지 타입 비교**

| 타입 | 물리적 배치 | 네트워크 | 내결함성 | 주요 용도 |
|------|-----------|---------|---------|---------|
| **Cluster** | 동일 랙/가용 영역 | 저지연, 최대 10Gbps 향상 | 낮음 (단일 랙) | HPC, ML 분산 학습, 빅데이터 |
| **Spread** | 서로 다른 물리 랙 | 일반 | 높음 (랙 격리) | 고가용성 소규모 (max 7/AZ) |
| **Partition** | 논리적 파티션 분리 | 일반 | 중간~높음 | Hadoop, Kafka, Cassandra |

**Cluster 배치 그룹**
- 모든 인스턴스가 동일한 물리 랙에 배치
- 인스턴스 간 네트워크 지연 최소화 (수 마이크로초)
- 동일 인스턴스 타입이어야 최적 성능 (혼용 시 성능 저하 가능)
- 단점: 랙 장애 시 전체 영향, 용량 확보 어려움

**Spread 배치 그룹**
- 각 인스턴스를 서로 다른 물리 랙에 배치
- AZ당 최대 7개 인스턴스 (엄격한 제한)
- 소규모 고가용성 서비스에 적합

**Partition 배치 그룹**
- 여러 파티션(논리적 그룹)으로 나누고, 각 파티션은 서로 다른 랙 사용
- AZ당 최대 7개 파티션, 파티션당 인스턴스 수 제한 없음
- 수백 대 이상 대규모 분산 시스템에 적합

---

### 2.2 실무 적용 코드

**Terraform — 배치 그룹 생성**

```hcl
# Cluster 배치 그룹
resource "aws_placement_group" "hpc_cluster" {
  name     = "hpc-cluster-pg"
  strategy = "cluster"
}

# Spread 배치 그룹
resource "aws_placement_group" "ha_spread" {
  name     = "ha-spread-pg"
  strategy = "spread"
}

# Partition 배치 그룹
resource "aws_placement_group" "kafka_partition" {
  name            = "kafka-partition-pg"
  strategy        = "partition"
  partition_count = 3    # Kafka 브로커 3개를 각 파티션에 배치
}
```

**Launch Template에 배치 그룹 연동**

```hcl
resource "aws_launch_template" "ml_node" {
  name_prefix   = "ml-node-"
  image_id      = data.aws_ami.al2023.id
  instance_type = "p3.8xlarge"

  placement {
    group_name = aws_placement_group.hpc_cluster.name
  }
}

resource "aws_autoscaling_group" "ml_nodes" {
  name                = "ml-cluster-asg"
  min_size            = 4
  max_size            = 4
  desired_capacity    = 4
  vpc_zone_identifier = [var.private_subnet_id_2a]  # Cluster는 단일 AZ

  launch_template {
    id      = aws_launch_template.ml_node.id
    version = "$Latest"
  }
}
```

**AWS CLI — 인스턴스를 배치 그룹에 시작**

```bash
# 배치 그룹 목록 확인
aws ec2 describe-placement-groups \
  --query 'PlacementGroups[*].{Name:GroupName,Strategy:Strategy,State:State}'

# Cluster 배치 그룹에 인스턴스 4개 동시 시작 (한 번에 시작해야 용량 확보 용이)
aws ec2 run-instances \
  --image-id ami-xxxxxxxx \
  --instance-type c5n.18xlarge \
  --count 4 \
  --placement GroupName=hpc-cluster-pg,AvailabilityZone=ap-northeast-2a \
  --subnet-id subnet-xxxxxxxx \
  --key-name my-key

# Spread 배치 그룹 — 인스턴스별로 다른 랙에 배치 확인
aws ec2 describe-instances \
  --filters "Name=placement-group-name,Values=ha-spread-pg" \
  --query 'Reservations[*].Instances[*].{ID:InstanceId,Host:Placement.HostId,AZ:Placement.AvailabilityZone}'
```

**EFA (Elastic Fabric Adapter) + Cluster 배치 그룹 조합**

```hcl
# HPC/ML 워크로드 최대 성능 구성
resource "aws_launch_template" "hpc_node" {
  name_prefix   = "hpc-efa-"
  instance_type = "c5n.18xlarge"   # EFA 지원 인스턴스

  # EFA 네트워크 인터페이스
  network_interfaces {
    device_index                = 0
    interface_type              = "efa"   # EFA 활성화
    subnet_id                   = var.private_subnet_2a
    security_groups             = [aws_security_group.hpc.id]
    delete_on_termination       = true
  }

  placement {
    group_name = aws_placement_group.hpc_cluster.name
  }
}
```

---

### 2.3 보안/비용 Best Practice

- **Cluster 그룹은 동일 AZ 필수**: 여러 AZ로 분산 불가
- **Spot + Spread**: Spot 인스턴스를 Spread 그룹에 배치하면 중단 위험 분산
- **배치 그룹은 변경 불가**: 생성 후 전략 변경 불가 — 재생성 필요
- **EFA는 Cluster 배치 그룹에서만 최대 성능**: 분산 ML 학습에서 NCCL 통신 가속

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**Cluster 그룹에서 인스턴스 시작 실패 — 용량 부족**

```bash
# 오류
# InsufficientInstanceCapacity: There is no Spot capacity available that matches your request.
# 또는
# InsufficientCapacityError for cluster placement group

# 원인: 해당 AZ의 물리 랙에 충분한 용량 없음
# 해결 1: 모든 인스턴스를 동시에 시작 (분할 시작보다 용량 확보 용이)
# 해결 2: 다른 AZ 시도
# 해결 3: 인스턴스 타입 변경 (용량이 더 풍부한 타입)
# 해결 4: Capacity Reservation 사전 예약
aws ec2 create-capacity-reservation \
  --instance-type c5n.18xlarge \
  --instance-platform Linux/UNIX \
  --availability-zone ap-northeast-2a \
  --instance-count 4 \
  --instance-match-criteria open
```

**Spread 그룹 최대 7개 제한 초과**

```bash
# 오류: GroupMaxInstanceCountExceeded
# 원인: AZ당 Spread 그룹은 최대 7개 인스턴스

# 해결: Partition 배치 그룹으로 전환 (파티션당 무제한)
# 또는 여러 Spread 그룹으로 분리
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: 배치 그룹 내 인스턴스를 다른 인스턴스 타입으로 혼용할 수 있나요?**
A: Cluster 배치 그룹에서는 혼용 가능하지만, 네트워크 성능은 가장 낮은 인스턴스 타입에 맞춰집니다. 동일 타입 사용을 권장합니다.

**Q: ASG에 배치 그룹을 적용하면 스케일 아웃 시 항상 같은 그룹에 시작되나요?**
A: 예. Launch Template에 배치 그룹을 지정하면 ASG의 새 인스턴스도 해당 그룹에 시작됩니다. 단, Cluster 그룹은 용량 부족으로 실패할 수 있습니다.

---

## 4. 모니터링 및 알람

```bash
# 배치 그룹 내 인스턴스 상태 확인
aws ec2 describe-instances \
  --filters "Name=placement-group-name,Values=hpc-cluster-pg" \
  --query 'Reservations[*].Instances[*].{ID:InstanceId,State:State.Name,AZ:Placement.AvailabilityZone}'

# EFA 네트워크 성능 측정 (배치 그룹 내)
# iperf3 서버
iperf3 -s

# iperf3 클라이언트 (같은 Cluster 그룹 내 다른 인스턴스에서)
iperf3 -c <server-ip> -t 30 -P 8
# Cluster 그룹: 최대 100Gbps (c5n.18xlarge 기준)
# 일반 인스턴스: 최대 25Gbps
```

---

## 5. TIP

- **배치 그룹은 삭제 후 재생성**: 전략 변경 불가이므로 인스턴스 모두 중지 → 배치 그룹 삭제 → 새 전략으로 재생성 → 인스턴스 시작
- **EKS에서 Cluster 배치 그룹**: ML 학습 전용 노드그룹에 배치 그룹 적용 — Karpenter EC2NodeClass에서도 설정 가능
- **Partition 그룹으로 Kafka 랙 인식**: `rack.id`를 파티션 번호로 설정하면 Kafka 레플리카가 서로 다른 물리 랙에 분산됨
