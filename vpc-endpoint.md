# VPC Endpoint (Gateway / Interface)

## 1. 개요

VPC Endpoint는 인터넷 게이트웨이나 NAT 없이 AWS 서비스에 프라이빗하게 접근하는 기능이다.
Gateway 타입(S3, DynamoDB)은 무료이고 라우팅 테이블에 추가되며,
Interface 타입(대부분의 서비스)은 AZ별 ENI를 생성해 비용이 발생하지만 보안성과 성능이 높다.
프라이빗 subnet의 EC2/EKS에서 NAT GW 트래픽을 줄여 비용을 절감하는 핵심 수단이다.

---

## 2. 설명

### 2.1 핵심 개념

**Gateway vs Interface Endpoint 비교**

| 항목 | Gateway Endpoint | Interface Endpoint (PrivateLink) |
|------|-----------------|----------------------------------|
| 지원 서비스 | S3, DynamoDB만 | EC2, ECR, SSM, Secrets Manager 등 100여 개 |
| 비용 | 무료 | $0.013/AZ/시간 + $0.01/GB |
| 동작 방식 | 라우팅 테이블에 prefix list 추가 | AZ별 ENI 생성, Private IP 부여 |
| DNS 변경 | 불필요 (기존 엔드포인트 그대로) | Private DNS 활성화 시 기존 DNS 재사용 가능 |
| 보안그룹 | 없음 | 적용 가능 |
| 접근 범위 | 같은 리전 | 같은 VPC 또는 VPN/DX 통해 온프레미스도 가능 |

**EKS/EC2에서 자주 쓰는 Interface Endpoint 목록**

| 서비스 | Endpoint 이름 | 필요 이유 |
|--------|-------------|---------|
| ECR API | `com.amazonaws.{region}.ecr.api` | 이미지 Pull 인증 |
| ECR DKR | `com.amazonaws.{region}.ecr.dkr` | 이미지 레이어 전송 |
| S3 | Gateway 타입으로 대체 | ECR 레이어는 S3에 저장됨 |
| SSM | `com.amazonaws.{region}.ssm` | Session Manager SSH 대체 |
| SSM Messages | `com.amazonaws.{region}.ssmmessages` | Session Manager 필수 |
| EC2 Messages | `com.amazonaws.{region}.ec2messages` | SSM Agent 필수 |
| Secrets Manager | `com.amazonaws.{region}.secretsmanager` | 시크릿 조회 |
| CloudWatch Logs | `com.amazonaws.{region}.logs` | 로그 전송 |
| STS | `com.amazonaws.{region}.sts` | IRSA AssumeRole |
| EKS | `com.amazonaws.{region}.eks` | EKS API 접근 |

**NAT GW vs Interface Endpoint 비용 비교 (ECR 예시)**

```
ECR에서 10GB/일 이미지 Pull (3개 AZ):

NAT GW 방식:
  NAT GW 처리 비용: 10GB × $0.059/GB = $0.59/일 = ~$18/월
  + NAT GW 시간 요금: $0.059 × 24h × 3AZ = ~$130/월

Interface Endpoint 방식:
  시간 요금: $0.013 × 24h × 3AZ = ~$28/월
  데이터 처리: 10GB × $0.01 = $0.10/일 = ~$3/월
  합계: ~$31/월

절감: ~$117/월 (대규모 EKS 클러스터에서는 훨씬 더 큼)
```

---

### 2.2 실무 적용 코드

**Terraform — Gateway Endpoint (S3, DynamoDB — 무료, 필수)**

```hcl
# S3 Gateway Endpoint
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"

  route_table_ids = concat(
    aws_route_table.private[*].id,
    [aws_route_table.public.id]
  )

  tags = { Name = "s3-gateway-endpoint" }
}

# DynamoDB Gateway Endpoint
resource "aws_vpc_endpoint" "dynamodb" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.region}.dynamodb"
  vpc_endpoint_type = "Gateway"

  route_table_ids = aws_route_table.private[*].id

  tags = { Name = "dynamodb-gateway-endpoint" }
}
```

**Terraform — Interface Endpoint (EKS 필수 세트)**

```hcl
# Interface Endpoint용 보안그룹
resource "aws_security_group" "vpc_endpoints" {
  name   = "vpc-endpoints-sg"
  vpc_id = aws_vpc.main.id

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.main.cidr_block]   # VPC 내부에서만 HTTPS 허용
  }

  tags = { Name = "vpc-endpoints-sg" }
}

locals {
  interface_endpoints = {
    ecr_api      = "com.amazonaws.${var.region}.ecr.api"
    ecr_dkr      = "com.amazonaws.${var.region}.ecr.dkr"
    ssm          = "com.amazonaws.${var.region}.ssm"
    ssmmessages  = "com.amazonaws.${var.region}.ssmmessages"
    ec2messages  = "com.amazonaws.${var.region}.ec2messages"
    logs         = "com.amazonaws.${var.region}.logs"
    sts          = "com.amazonaws.${var.region}.sts"
    secretsmanager = "com.amazonaws.${var.region}.secretsmanager"
    eks          = "com.amazonaws.${var.region}.eks"
  }
}

resource "aws_vpc_endpoint" "interface" {
  for_each = local.interface_endpoints

  vpc_id              = aws_vpc.main.id
  service_name        = each.value
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true   # 기존 AWS SDK endpoint 주소 그대로 사용 가능

  subnet_ids         = aws_subnet.private[*].id   # Private subnet에 ENI 생성
  security_group_ids = [aws_security_group.vpc_endpoints.id]

  tags = { Name = "${each.key}-endpoint" }
}
```

**Endpoint Policy — S3 접근 제한 (보안 강화)**

```hcl
# S3 Gateway Endpoint에 정책 추가 (특정 버킷만 허용)
resource "aws_vpc_endpoint" "s3_restricted" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = aws_route_table.private[*].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = "*"
        Action    = ["s3:GetObject", "s3:PutObject"]
        Resource = [
          "arn:aws:s3:::my-app-bucket/*",
          # ECR이 S3에서 이미지 레이어 받아오는 것 허용 (필수)
          "arn:aws:s3:::prod-${var.region}-starport-layer-bucket/*"
        ]
      }
    ]
  })
}
```

**온프레미스 → Interface Endpoint 접근 (DX/VPN 환경)**

```hcl
# Private DNS가 온프레미스에서는 동작 안 함
# → Route 53 Resolver Inbound Endpoint로 해결

resource "aws_route53_resolver_endpoint" "inbound" {
  name      = "inbound-resolver"
  direction = "INBOUND"

  security_group_ids = [aws_security_group.resolver.id]

  ip_address {
    subnet_id = aws_subnet.private[0].id
  }
  ip_address {
    subnet_id = aws_subnet.private[1].id
  }
}

# 온프레미스 DNS에서 *.amazonaws.com 쿼리를 이 IP로 포워딩
# → VPC 내 Route 53이 Private DNS로 Interface Endpoint IP 반환
```

---

### 2.3 보안/비용 Best Practice

- **S3, DynamoDB Gateway Endpoint는 무조건 생성**: 무료이고 NAT GW 데이터 처리 비용 절감. 모든 환경에서 기본 설정
- **ECR Interface Endpoint는 EKS 클러스터가 있으면 필수**: 이미지 Pull이 NAT GW를 타지 않아 비용 절감 + 속도 향상
- **Private DNS 활성화**: `private_dns_enabled = true`로 설정하면 코드 변경 없이 기존 SDK endpoint 사용. 단 VPC에 `enableDnsHostnames`, `enableDnsSupport` 설정 필요
- **Endpoint Policy로 최소 권한**: 기본은 전체 허용. Endpoint Policy로 특정 버킷·액션만 허용해 data exfiltration 방지

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**ECR에서 이미지 Pull 실패 (Private subnet)**

```bash
# 증상: EKS Pod가 ImagePullBackOff, "connection refused" 오류
# 원인: NAT GW가 없거나 Interface Endpoint 미설정

# Interface Endpoint 생성 확인
aws ec2 describe-vpc-endpoints \
  --filters "Name=vpc-id,Values=vpc-xxxxxxxx" \
  --query 'VpcEndpoints[*].{Service:ServiceName,State:State,DNS:DnsEntries[0].DnsName}'

# ECR Endpoint DNS 해석 확인 (Private subnet EC2에서)
nslookup 123456789012.dkr.ecr.ap-northeast-2.amazonaws.com
# → Private IP가 반환되어야 정상

# S3 Gateway Endpoint 없으면 ECR 레이어 Pull 실패
aws ec2 describe-vpc-endpoints \
  --filters "Name=service-name,Values=com.amazonaws.ap-northeast-2.s3" \
            "Name=vpc-endpoint-type,Values=Gateway"
```

**SSM Session Manager 연결 불가 (Private subnet)**

```bash
# 3개 Endpoint 모두 필요: ssm, ssmmessages, ec2messages
# + S3 Gateway Endpoint (Session 로그 저장용)

# SSM Agent 상태 확인 (EC2 내에서)
sudo systemctl status amazon-ssm-agent

# Endpoint 연결 테스트
curl -s https://ssm.ap-northeast-2.amazonaws.com/ping
# → {"status":"ok"} 가 나와야 정상
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: Interface Endpoint는 몇 개 AZ에 만들어야 하나요?**
A: 가용성을 위해 사용 중인 AZ 수만큼 생성 권장. 한 AZ의 Endpoint가 다운되면 해당 AZ 리소스는 다른 AZ Endpoint로 접근해 비용이 발생하고 지연이 늘어납니다.

**Q: Endpoint 비용이 예상보다 높게 나옵니다**
A: Interface Endpoint는 사용 여부와 관계없이 AZ당 시간 요금이 발생합니다. 사용 빈도가 낮은 개발 환경은 필요할 때만 생성하거나, 하나의 AZ에만 생성해 비용을 줄일 수 있습니다.

---

## 4. 모니터링 및 알람

```hcl
# Endpoint 상태 변경 감지
resource "aws_cloudwatch_event_rule" "endpoint_state_change" {
  name = "vpc-endpoint-state-change"

  event_pattern = jsonencode({
    source      = ["aws.ec2"]
    detail-type = ["AWS API Call via CloudTrail"]
    detail = {
      eventName = ["DeleteVpcEndpoints", "ModifyVpcEndpoint"]
    }
  })
}

resource "aws_cloudwatch_event_target" "endpoint_alert" {
  rule      = aws_cloudwatch_event_rule.endpoint_state_change.name
  target_id = "AlertSNS"
  arn       = aws_sns_topic.alerts.arn
}
```

**주요 CloudWatch 지표**

| 지표 | 네임스페이스 | 설명 |
|------|------------|------|
| `BytesProcessed` | `AWS/PrivateLinkEndpoints` | Endpoint 처리 데이터량 |
| `PacketsDropped` | `AWS/PrivateLinkEndpoints` | 드롭된 패킷 (보안그룹 거부 포함) |

---

## 5. TIP

- **Endpoint 생성 전 비용 계산**: Interface Endpoint 10개 × 3AZ = 30개 ENI × $0.013 = $0.39/시간 ≈ $280/월. 실제 NAT GW 절감액과 비교 후 결정
- **PrivateLink vs Transit GW**: 계정 간 서비스 공유 시 Transit GW 대신 PrivateLink(Endpoint Service) 사용하면 트래픽이 AWS 백본을 벗어나지 않음
- **Endpoint 생성 자동화**: AWS Config Rule로 특정 서비스의 Endpoint가 없는 VPC를 탐지하고 자동 알람 가능
