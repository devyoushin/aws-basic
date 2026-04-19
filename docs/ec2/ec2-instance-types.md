# EC2 인스턴스 타입 선택 가이드

## 1. 개요

EC2 인스턴스 타입은 CPU, 메모리, 네트워크, 스토리지 특성에 따라 다양한 패밀리로 나뉜다.
워크로드 특성에 맞는 타입을 선택하는 것이 성능과 비용 최적화의 핵심이며,
Graviton(ARM64) 기반 인스턴스는 동일 가격 대비 성능이 뛰어나 적극 검토할 가치가 있다.

---

## 2. 설명

### 2.1 핵심 개념

**인스턴스 이름 구조**

```
  m  5  .  x  l  a  r  g  e
  │  │     │  └─ 크기 (medium, large, xlarge, 2xlarge, ...)
  │  │     └─ 추가 기능 접미사 (n=네트워크, d=로컬 NVMe, a=AMD, g=Graviton, z=고주파수)
  │  └─ 세대 (숫자가 클수록 최신)
  └─ 패밀리 (m=범용, c=컴퓨팅, r=메모리, i=스토리지, p/g=GPU 등)
```

**패밀리별 특성 요약**

| 패밀리 | 특성 | 주요 용도 |
|--------|------|---------|
| **m** (범용) | CPU:Memory = 1:4 균형 | 웹 애플리케이션, 마이크로서비스 |
| **t** (버스트) | 낮은 기준 CPU + 크레딧 버스트 | 개발/테스트, 저트래픽 서비스 |
| **c** (컴퓨팅) | CPU:Memory = 1:2 고성능 CPU | 배치 처리, 게임 서버, 미디어 변환 |
| **r** (메모리) | CPU:Memory = 1:8 대용량 RAM | 인메모리 DB, Redis, 캐시 서버 |
| **x** (대용량 메모리) | 최대 수 TB RAM | SAP HANA, Oracle 인메모리 |
| **i** (스토리지 최적화) | 고속 NVMe SSD | 고IOPS DB, NoSQL |
| **d** (HDD 최적화) | 고밀도 HDD | 빅데이터, HDFS |
| **p** (GPU 범용) | NVIDIA GPU | ML 학습, 렌더링 |
| **g** (그래픽 GPU) | NVIDIA GPU | 게임 스트리밍, 그래픽 |
| **inf** (AI 추론) | AWS Inferentia | ML 추론 (저비용) |
| **trn** (AI 학습) | AWS Trainium | ML 학습 (저비용) |

**세대별 주요 변화**

| 세대 | CPU | 특징 |
|------|-----|------|
| 5 | Intel Xeon Skylake / AMD EPYC | Nitro 시스템 |
| 6i | Intel Ice Lake | 15% 성능 향상 대비 5세대 |
| 6a | AMD EPYC Milan | Intel 대비 10% 저렴 |
| 6g | AWS Graviton2 (ARM64) | Intel 대비 40% 높은 가격/성능 |
| 7i/7a/7g | 최신 세대 | 6세대 대비 15~30% 향상 |

**Graviton (ARM64) — 강력 추천**

| 항목 | x86 (Intel/AMD) | Graviton (ARM64) |
|------|----------------|-----------------|
| 가격 (m7 기준) | 기준 | 약 20% 저렴 |
| 성능 | 기준 | 동일~30% 향상 (워크로드 따라 다름) |
| 아키텍처 | x86_64 | aarch64 (arm64) |
| 컨테이너 이미지 | 기존 그대로 | arm64 빌드 필요 (또는 멀티 아키텍처) |
| 주요 지원 | 모든 소프트웨어 | Java, Python, Go, Node.js 완벽 지원 |

---

### 2.2 실무 적용 코드

**워크로드별 추천 인스턴스 타입**

```hcl
# 1. 웹 애플리케이션 / 마이크로서비스 (범용)
# 권장: m7g.xlarge (Graviton3, ARM64) 또는 m7i.xlarge
resource "aws_launch_template" "web" {
  instance_type = "m7g.xlarge"   # Graviton3, x86 대비 20% 저렴
}

# 2. 배치 처리 / 데이터 처리 (컴퓨팅 최적화)
# 권장: c7g.2xlarge 또는 c6i.2xlarge
resource "aws_launch_template" "batch" {
  instance_type = "c7g.2xlarge"  # 고성능 CPU, Graviton3
}

# 3. Redis / Memcached / 인메모리 DB (메모리 최적화)
# 권장: r7g.xlarge 또는 r6i.xlarge
resource "aws_launch_template" "cache" {
  instance_type = "r7g.xlarge"   # 8GB RAM/vCPU 비율
}

# 4. ML 학습 (GPU)
# 권장: p3.8xlarge (V100) 또는 p4d.24xlarge (A100)
resource "aws_launch_template" "ml_training" {
  instance_type = "p3.8xlarge"
}

# 5. ML 추론 (비용 효율적)
# 권장: inf2.xlarge (AWS Inferentia2)
resource "aws_launch_template" "ml_inference" {
  instance_type = "inf2.xlarge"  # NVIDIA GPU 대비 최대 4배 저렴
}
```

**t 계열 — 크레딧 모드 설정**

```hcl
resource "aws_instance" "dev" {
  ami           = data.aws_ami.al2023.id
  instance_type = "t3.medium"

  # unlimited: 크레딧 소진 후에도 성능 유지 (추가 요금 발생)
  # standard: 크레딧 소진 시 기준 성능으로 제한 (기본값)
  credit_specification {
    cpu_credits = "unlimited"  # 개발 환경에서 크레딧 부족 방지
  }
}
```

**Graviton 멀티 아키텍처 Docker 이미지 빌드**

```dockerfile
# 멀티 아키텍처 이미지 빌드 (x86 + arm64 동시 지원)
FROM --platform=$BUILDPLATFORM golang:1.21 AS builder
ARG TARGETPLATFORM
ARG BUILDPLATFORM

WORKDIR /app
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o myapp .

FROM --platform=$TARGETPLATFORM alpine:latest
COPY --from=builder /app/myapp /usr/local/bin/
CMD ["myapp"]
```

```bash
# Docker Buildx로 멀티 아키텍처 빌드 및 ECR 푸시
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --tag 123456789.dkr.ecr.ap-northeast-2.amazonaws.com/myapp:latest \
  --push .
```

**Compute Optimizer — Right-sizing 권고 확인**

```bash
# Compute Optimizer 권고 확인
aws compute-optimizer get-ec2-instance-recommendations \
  --instance-arns arn:aws:ec2:ap-northeast-2:123456789:instance/i-xxxxxxxx \
  --query 'instanceRecommendations[*].{
    Current:currentInstanceType,
    Recommended:recommendationOptions[0].instanceType,
    Savings:recommendationOptions[0].estimatedMonthlySavings.value
  }'

# 계정 내 모든 인스턴스 over-provisioning 보고서
aws compute-optimizer get-ec2-instance-recommendations \
  --filters name=Finding,values=Overprovisioned \
  --query 'instanceRecommendations[*].{
    Instance:instanceArn,
    Current:currentInstanceType,
    Recommended:recommendationOptions[0].instanceType
  }'
```

---

### 2.3 보안/비용 Best Practice

**비용 최적화 3-tier 전략**

```
Reserved Instances / Savings Plans (안정적 기준 부하)
    ↕ Compute Savings Plans: 인스턴스 타입/리전 무관
On-Demand (예측 불가 급증)
    ↕ 짧은 급증 처리
Spot (배치 작업, 장애 허용 워크로드)
    ↕ 최대 90% 절감
```

- **Graviton 우선 검토**: 새 서비스 배포 시 ARM64 호환성 먼저 확인 → 호환되면 Graviton 선택
- **t 계열은 개발/테스트 전용**: 프로덕션에서 t 계열 사용 시 크레딧 고갈 위험
- **신세대 인스턴스 사용**: 동일 비용으로 5세대보다 6~7세대가 성능 우수

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**t 계열 CPU 크레딧 고갈**

```bash
# CPUCreditBalance 확인
aws cloudwatch get-metric-statistics \
  --namespace AWS/EC2 \
  --metric-name CPUCreditBalance \
  --dimensions Name=InstanceId,Value=i-xxxxxxxx \
  --start-time $(date -u -v-1d +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 3600 \
  --statistics Minimum

# 크레딧 잔량이 0에 가까우면 기준 성능으로 제한됨
# 해결 1: unlimited 모드 전환 (추가 요금)
aws ec2 modify-instance-credit-specification \
  --instance-credit-specifications "InstanceId=i-xxxxxxxx,CpuCredits=unlimited"

# 해결 2: m5.large 등 크레딧 없는 인스턴스로 교체 (권장)
```

**Graviton 아키텍처 호환성 오류**

```bash
# 오류: "exec format error"
# 원인: x86_64 빌드 이진 파일을 ARM64 인스턴스에서 실행 시도

# 이미지 아키텍처 확인
docker manifest inspect my-image:latest | jq '.[].platform'

# arm64 이미지 빌드 여부 확인
aws ecr describe-images \
  --repository-name my-repo \
  --image-ids imageTag=latest \
  --query 'imageDetails[*].imageScanStatus'

# 해결: 멀티 아키텍처 빌드로 재빌드
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: m5 vs m6i vs m7i 중 어떤 걸 써야 하나요?**
A: 신규 배포라면 최신 세대(m7i 또는 m7g)를 우선 선택하세요. 비용 대비 성능이 가장 우수합니다. 기존에 m5를 쓰고 있다면 m6i/m7i로 교체 시 동일 비용에 15~30% 성능 향상을 기대할 수 있습니다.

**Q: Graviton 인스턴스에서 Java 애플리케이션이 느립니다**
A: Java 17+ 버전은 Graviton을 잘 지원합니다. JDK 버전을 최신으로 업데이트하고, Corretto 또는 Temurin (ARM 최적화) 사용을 권장합니다. JVM 플래그: `-XX:+UseG1GC -XX:MaxRAMPercentage=75.0`

---

## 4. 모니터링 및 알람

```hcl
# t 계열 CPU 크레딧 잔량 알람
resource "aws_cloudwatch_metric_alarm" "cpu_credit_low" {
  alarm_name          = "t-instance-cpu-credit-low"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  metric_name         = "CPUCreditBalance"
  namespace           = "AWS/EC2"
  period              = 1800   # 30분
  statistic           = "Minimum"
  threshold           = 20     # 크레딧 20개 미만 시 알람

  dimensions = {
    InstanceId = aws_instance.dev.id
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
}
```

---

## 5. TIP

- **인스턴스 타입 비교 사이트**: [instances.vantage.sh](https://instances.vantage.sh) — 전체 인스턴스 타입을 가격, 스펙, 리전별로 비교 가능
- **Savings Plans 계산기**: AWS 콘솔 → Compute Optimizer → Savings Plans recommendations에서 현재 사용 패턴 기반 최적 Savings Plans 추천 확인
- **EKS에서 Graviton 적용**: Karpenter NodePool의 `requirements`에 `kubernetes.io/arch: arm64` 추가로 Graviton 노드만 프로비저닝

```yaml
# Karpenter NodePool — Graviton 우선
spec:
  requirements:
    - key: kubernetes.io/arch
      operator: In
      values: ["arm64", "amd64"]   # arm64 우선, 없으면 amd64
    - key: karpenter.k8s.aws/instance-generation
      operator: Gt
      values: ["5"]                 # 6세대 이상만
```
