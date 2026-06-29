## 1. 개요

Network Load Balancer(NLB)는 과거에는 Security Group(보안 그룹)을 직접 연결할 수 없어 Target EC2의 Security Group에서 client CIDR 또는 NLB subnet CIDR을 허용하는 방식으로 운영했다. 현재는 NLB에도 Security Group을 연결할 수 있으므로, **NLB Security Group에서 client ingress를 제어하고 Target Security Group은 NLB Security Group만 source로 허용**하는 패턴이 Best Practice다.

핵심은 두 단계로 나뉜다.

| 계층 | 역할 | 권장 관리 방식 |
|---|---|---|
| NLB Security Group | 외부/내부 client가 NLB listener에 접근 가능한지 제어 | client CIDR, VPC CIDR, Prefix List 기준으로 inbound 제한 |
| Target Security Group | NLB가 EC2/ECS/IP target에 접근 가능한지 제어 | source를 NLB Security Group으로 지정 |

이 구조를 사용하면 target instance가 인터넷 또는 VPC 내부 client에서 직접 호출되는 것을 막고, 반드시 NLB를 통과한 트래픽만 허용할 수 있다.

---

## 2. 설명

### 2.1 NLB Security Group 동작 원리

NLB에 Security Group을 연결하면 NLB listener로 들어오는 트래픽과 NLB에서 나가는 트래픽을 Security Group rule로 제어한다.

| 항목 | 동작 |
|---|---|
| Inbound rule | client → NLB listener 트래픽 허용/차단 |
| Outbound rule | NLB → target 트래픽, health check 트래픽 허용/차단 |
| Target SG source 참조 | Target EC2/ECS/IP가 NLB SG에서 온 트래픽만 허용 |
| Client IP preservation | target SG가 NLB SG를 source로 참조하면 client IP 보존 여부와 관계없이 NLB 경유 트래픽 허용 |
| PrivateLink traffic | NLB inbound rule 적용 여부를 별도 옵션으로 제어 가능 |

중요한 제약은 **NLB 생성 시 Security Group을 하나도 연결하지 않으면 나중에 Security Group을 연결할 수 없다는 점**이다. 운영 NLB는 처음 생성할 때 비어 있는 placeholder SG라도 연결해 두는 것이 안전하다. 생성 시 SG를 연결한 NLB는 이후 SG 교체/추가/삭제가 가능하다.

### 2.2 권장 inbound rule 패턴

NLB SG의 inbound rule은 listener port 기준으로 client source를 제한한다.

| NLB 유형 | Source 권장값 | 예시 |
|---|---|---|
| Internet-facing public API | 허용된 office/VPN/WAF/NAT CIDR | `203.0.113.10/32`, `198.51.100.0/24` |
| Internet-facing 공개 서비스 | `0.0.0.0/0`, `::/0` 단, TLS/Shield/WAF 대안 검토 | TCP 443 공개 |
| Internal NLB | VPC CIDR 또는 caller subnet CIDR | `10.0.0.0/16` |
| Multi VPC/TGW/DX 경유 | 상대 VPC CIDR, on-prem CIDR, managed prefix list | `10.20.0.0/16`, `pl-xxxx` |
| PrivateLink Provider | endpoint consumer IP 정책에 따라 inbound rule 적용 여부 결정 | `EnforceSecurityGroupInboundRulesOnPrivateLinkTraffic` |

NLB SG의 outbound rule은 target port와 health check port를 막지 않아야 한다. AWS 문서 기준 health check는 inbound rule의 영향을 받지 않지만 outbound rule의 영향을 받는다.

### 2.3 Target Security Group Best Practice

Target SG는 client CIDR을 직접 열지 않고 NLB SG를 source로 참조한다.

| Rule | Source | Port | 목적 |
|---|---|---|---|
| target port | NLB SG ID | app port | NLB가 target으로 실제 트래픽 전달 |
| health check port | NLB SG ID | health check port | NLB target health check 통과 |
| SSH/SSM | bastion SG 또는 SSM 사용 | 22 또는 없음 | 운영 접근 분리 |

이 방식의 장점은 아래와 같다.

| 장점 | 설명 |
|---|---|
| 직접 접근 차단 | target instance의 app port를 client CIDR에 직접 열지 않음 |
| client IP preservation 대응 | target이 보는 source IP와 무관하게 NLB SG 참조로 허용 가능 |
| CIDR 관리 축소 | NLB subnet IP, client CIDR 변화를 target SG에 반영하지 않음 |
| 운영 책임 분리 | NLB SG는 client ingress, target SG는 NLB 경유 여부만 관리 |

### 2.4 Terraform 예시

```hcl
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  required_version = ">= 1.6"
}

variable "vpc_id" {
  type = string
}

variable "public_subnet_ids" {
  type = list(string)
}

variable "allowed_client_cidrs" {
  type = list(string)
}

resource "aws_security_group" "prod_nlb_sg" {
  name        = "prod-nlb-sg"
  description = "Control client ingress to production NLB"
  vpc_id      = var.vpc_id

  tags = {
    Name        = "prod-nlb-sg"
    Environment = "prod"
    Team        = "<TEAM_NAME>"
    ManagedBy   = "terraform"
  }
}

resource "aws_vpc_security_group_ingress_rule" "prod_nlb_https" {
  for_each = toset(var.allowed_client_cidrs)

  security_group_id = aws_security_group.prod_nlb_sg.id
  description       = "Allow HTTPS clients to NLB listener"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = each.value
}

resource "aws_vpc_security_group_ingress_rule" "prod_nlb_icmp_pmtu" {
  security_group_id = aws_security_group.prod_nlb_sg.id
  description       = "Allow ICMP for Path MTU Discovery"
  ip_protocol       = "icmp"
  from_port         = -1
  to_port           = -1
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "prod_nlb_egress_all" {
  security_group_id = aws_security_group.prod_nlb_sg.id
  description       = "Allow NLB to reach targets and health check port"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_security_group" "prod_app_sg" {
  name        = "prod-app-sg"
  description = "Allow traffic only from production NLB"
  vpc_id      = var.vpc_id

  tags = {
    Name        = "prod-app-sg"
    Environment = "prod"
    Team        = "<TEAM_NAME>"
    ManagedBy   = "terraform"
  }
}

resource "aws_vpc_security_group_ingress_rule" "prod_app_from_nlb_app" {
  security_group_id            = aws_security_group.prod_app_sg.id
  description                  = "Allow app traffic from NLB"
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
  referenced_security_group_id = aws_security_group.prod_nlb_sg.id
}

resource "aws_vpc_security_group_ingress_rule" "prod_app_from_nlb_health_check" {
  security_group_id            = aws_security_group.prod_app_sg.id
  description                  = "Allow health check from NLB"
  ip_protocol                  = "tcp"
  from_port                    = 8081
  to_port                      = 8081
  referenced_security_group_id = aws_security_group.prod_nlb_sg.id
}

resource "aws_vpc_security_group_egress_rule" "prod_app_egress_all" {
  security_group_id = aws_security_group.prod_app_sg.id
  description       = "Allow outbound"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_lb" "prod_nlb" {
  name               = "prod-app-nlb"
  load_balancer_type = "network"
  internal           = false
  subnets            = var.public_subnet_ids
  security_groups    = [aws_security_group.prod_nlb_sg.id]

  enable_deletion_protection = true

  tags = {
    Name        = "prod-app-nlb"
    Environment = "prod"
    Team        = "<TEAM_NAME>"
    ManagedBy   = "terraform"
  }
}

resource "aws_lb_target_group" "prod_app_tg" {
  name        = "prod-app-tg"
  port        = 8080
  protocol    = "TCP"
  vpc_id      = var.vpc_id
  target_type = "instance"

  health_check {
    enabled             = true
    protocol            = "TCP"
    port                = "8081"
    healthy_threshold   = 3
    unhealthy_threshold = 3
    interval            = 30
  }
}

resource "aws_lb_listener" "prod_tls" {
  load_balancer_arn = aws_lb.prod_nlb.arn
  port              = 443
  protocol          = "TCP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.prod_app_tg.arn
  }
}
```

### 2.5 AWS CLI 운영 명령

기존 NLB가 생성 시 SG를 연결한 상태라면 SG를 교체할 수 있다.

```bash
aws elbv2 set-security-groups \
  --load-balancer-arn <NLB_ARN> \
  --security-groups <NLB_SECURITY_GROUP_ID> \
  --region ap-northeast-2 \
  --output json
```

NLB 생성 시 SG가 없던 경우에는 위 명령으로 SG 연결이 불가능하다. 이 경우 새 NLB를 생성하고 DNS/Route 53 또는 upstream 설정을 전환한다.

PrivateLink traffic에 NLB inbound rule을 적용하지 않으려면 아래 옵션을 사용한다.

```bash
aws elbv2 set-security-groups \
  --load-balancer-arn <NLB_ARN> \
  --security-groups <NLB_SECURITY_GROUP_ID> \
  --enforce-security-group-inbound-rules-on-private-link-traffic off \
  --region ap-northeast-2 \
  --output json
```

NLB SG와 target SG의 실제 rule을 확인한다.

```bash
aws ec2 describe-security-groups \
  --group-ids <NLB_SECURITY_GROUP_ID> <TARGET_SECURITY_GROUP_ID> \
  --region ap-northeast-2 \
  --output json
```

### 2.6 inbound rule 관리 원칙

| 원칙 | 설명 |
|---|---|
| NLB 생성 시 SG 필수 연결 | 나중에 SG를 붙일 수 없는 상태 방지 |
| listener port만 inbound 허용 | NLB SG에 불필요한 port open 금지 |
| target SG는 NLB SG만 source 허용 | client direct access 차단 |
| health check port 별도 명시 | app port와 health check port가 다르면 둘 다 허용 |
| ICMP 허용 검토 | Path MTU Discovery 지원 |
| Prefix List 사용 | office/VPN/on-prem CIDR 변경을 중앙 관리 |
| Terraform/IaC 관리 | 콘솔 수동 변경 drift 방지 |
| CloudWatch blocked flow metric 알람 | SG 차단으로 인한 장애 조기 탐지 |

---

## 3. 트러블슈팅

### 증상
- `set-security-groups`로 기존 NLB에 SG를 연결하려고 하면 실패함

### 원인
- NLB 생성 시 Security Group을 하나도 연결하지 않았음. AWS 제약상 이 경우 이후에 SG를 연결할 수 없음

### 해결 방법

```bash
aws elbv2 describe-load-balancers \
  --load-balancer-arns <NLB_ARN> \
  --query 'LoadBalancers[0].SecurityGroups' \
  --region ap-northeast-2 \
  --output json
```

반환값이 비어 있고 SG 연결 변경이 실패하면 새 NLB를 생성한다. 운영 표준은 NLB 생성 시 placeholder SG라도 연결하는 것이다.

---

### 증상
- NLB DNS로 접근 시 timeout 또는 connection refused 발생

### 원인
- NLB SG inbound에서 client source CIDR 또는 listener port를 허용하지 않음
- NLB SG outbound에서 target port를 차단함
- Target SG에서 NLB SG source를 허용하지 않음

### 해결 방법

```bash
aws ec2 describe-security-groups \
  --group-ids <NLB_SECURITY_GROUP_ID> <TARGET_SECURITY_GROUP_ID> \
  --region ap-northeast-2 \
  --output json

aws elbv2 describe-target-health \
  --target-group-arn <TARGET_GROUP_ARN> \
  --region ap-northeast-2 \
  --output json
```

NLB SG inbound는 client source → listener port, target SG inbound는 NLB SG → target port 구조인지 확인한다.

---

### 증상
- Target health check가 계속 unhealthy임

### 원인
- Target SG에서 health check port를 허용하지 않음
- NLB SG outbound가 health check traffic을 차단함
- Application이 health check port에서 listen하지 않음

### 해결 방법

```bash
aws elbv2 describe-target-health \
  --target-group-arn <TARGET_GROUP_ARN> \
  --targets Id=<TARGET_ID>,Port=<TARGET_PORT> \
  --region ap-northeast-2 \
  --output json
```

health check port가 app target port와 다르면 target SG에 두 port를 모두 명시한다.

---

### 증상
- PrivateLink consumer traffic이 NLB SG inbound rule에 의해 차단됨

### 원인
- NLB의 PrivateLink traffic inbound rule enforcement가 켜져 있음. 이때 source는 endpoint interface가 아니라 client private IP로 평가됨

### 해결 방법

```bash
aws elbv2 set-security-groups \
  --load-balancer-arn <NLB_ARN> \
  --security-groups <NLB_SECURITY_GROUP_ID> \
  --enforce-security-group-inbound-rules-on-private-link-traffic off \
  --region ap-northeast-2 \
  --output json
```

보안 요구사항상 PrivateLink traffic도 inbound rule로 통제해야 하면 consumer VPC CIDR 또는 허용 client CIDR을 NLB SG inbound에 반영한다.

---

## 4. 모니터링 및 알람

### 4.1 CloudWatch 지표

| Metric | Namespace | 의미 |
|---|---|---|
| `SecurityGroupBlockedFlowCount_Inbound` | `AWS/NetworkELB` | NLB SG inbound rule에 의해 차단된 flow 수 |
| `SecurityGroupBlockedFlowCount_Outbound` | `AWS/NetworkELB` | NLB SG outbound rule에 의해 차단된 flow 수 |
| `HealthyHostCount` | `AWS/NetworkELB` | 정상 target 수 |
| `UnHealthyHostCount` | `AWS/NetworkELB` | 비정상 target 수 |
| `TCP_Client_Reset_Count` | `AWS/NetworkELB` | client reset 증가 여부 |
| `TCP_Target_Reset_Count` | `AWS/NetworkELB` | target reset 증가 여부 |

### 4.2 Terraform CloudWatch alarm 예시

```hcl
resource "aws_cloudwatch_metric_alarm" "prod_nlb_sg_blocked_inbound" {
  alarm_name          = "prod-nlb-sg-blocked-inbound"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "SecurityGroupBlockedFlowCount_Inbound"
  namespace           = "AWS/NetworkELB"
  period              = 60
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "NLB security group is blocking inbound flows"
  alarm_actions       = [aws_sns_topic.ops_alert.arn]

  dimensions = {
    LoadBalancer = "<NLB_FULL_NAME>"
  }

  tags = {
    Name        = "prod-nlb-sg-blocked-inbound"
    Environment = "prod"
    Team        = "<TEAM_NAME>"
    ManagedBy   = "terraform"
  }
}

resource "aws_cloudwatch_metric_alarm" "prod_nlb_unhealthy_hosts" {
  alarm_name          = "prod-nlb-unhealthy-hosts"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "UnHealthyHostCount"
  namespace           = "AWS/NetworkELB"
  period              = 60
  statistic           = "Maximum"
  threshold           = 0
  alarm_description   = "NLB has unhealthy targets"
  alarm_actions       = [aws_sns_topic.ops_alert.arn]

  dimensions = {
    LoadBalancer = "<NLB_FULL_NAME>"
    TargetGroup  = "<TARGET_GROUP_FULL_NAME>"
  }

  tags = {
    Name        = "prod-nlb-unhealthy-hosts"
    Environment = "prod"
    Team        = "<TEAM_NAME>"
    ManagedBy   = "terraform"
  }
}
```

### 4.3 VPC Flow Logs 확인

NLB SG에서 차단된 traffic은 CloudWatch blocked flow metric과 VPC Flow Logs를 함께 확인한다.

```sql
SELECT
  start,
  end,
  srcaddr,
  dstaddr,
  dstport,
  action,
  packets,
  bytes
FROM vpc_flow_logs
WHERE action = 'REJECT'
  AND dstport IN (443, 8080, 8081)
ORDER BY start DESC
LIMIT 100;
```

---

## 5. TIP

- 운영 NLB는 생성 시 반드시 Security Group을 연결함. 나중에 붙일 수 없는 상태를 만들지 않는 것이 가장 중요함.
- Target SG에서 client CIDR을 직접 열지 말고 NLB SG를 source로 참조함. 이 패턴이 client IP preservation과 직접 접근 차단을 동시에 해결함.
- NLB SG inbound는 “누가 NLB listener에 들어올 수 있는가”, target SG inbound는 “누가 backend target에 들어올 수 있는가”로 책임을 분리함.
- public NLB에서 `0.0.0.0/0` inbound가 필요한 경우에도 listener port만 열고, TLS listener, Shield, WAF 대안 구조, application authentication을 함께 검토함. NLB 자체에는 AWS WAF를 직접 붙일 수 없으므로 L7 방어가 필요하면 ALB 또는 CloudFront 전단 구성을 검토함.
- PrivateLink Provider NLB는 inbound rule enforcement 옵션을 명시적으로 결정함. consumer CIDR이 겹치거나 예측하기 어려운 환경에서는 운영 정책을 별도 문서화함.
- 관련 공식 문서:
  - [Update the security groups for your Network Load Balancer](https://docs.aws.amazon.com/elasticloadbalancing/latest/network/load-balancer-security-groups.html)
  - [Create a Network Load Balancer](https://docs.aws.amazon.com/elasticloadbalancing/latest/network/create-network-load-balancer.html)
  - [Register targets for your Network Load Balancer](https://docs.aws.amazon.com/elasticloadbalancing/latest/network/target-group-register-targets.html)
