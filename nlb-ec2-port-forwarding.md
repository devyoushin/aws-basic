## 1. 개요

본 문서는 AWS **Network Load Balancer(NLB)**를 사용하여 외부 트래픽을 수신하고, 이를 프라이빗 서브넷에 위치한 **EC2 인스턴스의 특정 포트로 전달(Port Forwarding)**하는 아키텍처 구축 방법을 설명합니다. NLB는 Layer 4(TCP/UDP)에서 동작하며 극도로 낮은 지연 시간과 고성능이 필요한 워크로드에 적합합니다.

## 2. 설명

### 2.1 주요 아키텍처 구성

- **NLB**: 고정 IP(Elastic IP)를 가질 수 있으며, 대규모 트래픽 처리에 최적화됨.
    
- **Target Group**: NLB가 트래픽을 전달할 EC2 인스턴스와 포트를 정의.
    
- **Security Group**: NLB 자체에는 보안 그룹 설정이 가능(최신 기능)하거나, 타겟 EC2에서 NLB의 IP 대역을 허용해야 함.
    

### 2.2 실무 적용 코드 

```hcl
# 1. Target Group 설정 (예: 8080 포트로 포워딩)
resource "aws_lb_target_group" "app_tg" {
  name        = "tg-prod-app-api"
  port        = 8080
  protocol    = "TCP"
  vpc_id      = var.vpc_id
  target_type = "instance"

  health_check {
    protocol            = "TCP"
    interval            = 30
    healthy_threshold   = 3
    unhealthy_threshold = 3
  }
}

# 2. NLB 설정
resource "aws_lb" "nlb" {
  name               = "nlb-prod-external"
  internal           = false
  load_balancer_type = "network"
  subnets            = var.public_subnets

  enable_deletion_protection = true # 운영 환경 필수
}

# 3. Listener 설정 (80 포트로 들어온 트래픽을 8080으로 전달)
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.nlb.arn
  port              = "80"
  protocol          = "TCP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app_tg.arn
  }
}

# 4. EC2 보안 그룹 설정
resource "aws_security_group" "app_sg" {
  name   = "sg-app-server"
  vpc_id = var.vpc_id

  ingress {
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    # NLB는 Client IP를 유지하므로, NLB의 서브넷 대역 또는 0.0.0.0/0 허용이 필요할 수 있음
    cidr_blocks = ["0.0.0.0/0"] 
  }
}
```

## 3. 트러블슈팅 및 모니터링 전략

### 3.1 주요 모니터링 지표 (CloudWatch)

- **HealthyHostCount**: 타겟 그룹 내 정상 상태인 인스턴스 수. 1 미만일 경우 즉각 알람 발생 필요.
    
- **UnHealthyHostCount**: 상태 검사(Health Check) 실패 인스턴스 수.
    
- **ProcessedBytes**: NLB를 통해 처리된 트래픽 양(비용 모니터링 용도).
    

### 3.2 장애 대응 시나리오

1. **상태 검사 실패 (Unhealthy Targets)**:
    
    - **원인**: EC2 내부 애플리케이션 미기동 또는 보안 그룹에서 타겟 포트(8080) 차단.
        
    - **해결**: `curl -v localhost:8080`으로 로컬 확인 후 SG 설정 재검토.
        
2. **연결 타임아웃 (Connection Timeout)**:
    
    - **원인**: NLB 보안 그룹(활성화 시)에서 아웃바운드 차단 혹은 OS 방화벽(iptables) 문제.
        

---

## 4. 참고자료

- [AWS Documentation: What is a Network Load Balancer?](https://docs.aws.amazon.com/elasticloadbalancing/latest/network/introduction.html)
    
- [Terraform Registry: aws_lb](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/lb)
    

## TIP (Best Practice)

### 🛡️ 보안 (Security)

- **Preserve Client IP**: NLB는 기본적으로 클라이언트의 소스 IP를 유지합니다. EC2 보안 그룹 설정 시 NLB의 프라이빗 IP가 아닌 실제 클라이언트 IP가 들어온다는 점을 유의하세요.
    
- **TLS Termination**: 보안 강화가 필요하다면 NLB에서 TLS를 종료(Termination)하여 EC2까지는 암호화된 트래픽을 전달하거나 내부망 보안을 유지하세요.
    

### 💰 비용 (Cost Optimization)

- **Cross-Zone Load Balancing**: 가용 영역(AZ) 간 트래픽 전달 시 데이터 이전 비용이 발생할 수 있습니다. 트래픽이 매우 많다면 해당 옵션을 비활성화하되, 타겟 그룹의 가용성을 면밀히 모니터링해야 합니다.
    
- **Idle Connection**: 불필요한 리스너를 삭제하여 LCU(Load Balancer Capacity Units) 비용을 절감하세요.
