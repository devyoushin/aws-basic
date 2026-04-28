# Private Subnet에서 DNF/YUM 패키지 설치 — VPC Endpoint & RHUI & VPC Lattice

## 1. 개요

인터넷 게이트웨이(IGW)나 NAT 없이 격리된 Private Subnet EC2에서 `dnf install` / `yum install` 을 실행하려면,
AWS가 리전 내부에 호스팅하는 패키지 저장소에 프라이빗하게 접근해야 한다.

OS 종류에 따라 메커니즘이 다르다.

| OS | 패키지 저장소 | 프라이빗 접근 방법 |
|----|-------------|-----------------|
| Amazon Linux 2 / AL2023 | S3 버킷 (cdn.amazonlinux.com) | **S3 Gateway Endpoint** |
| RHEL (AWS Marketplace) | AWS-hosted RHUI 서버 | **RHUI IP 직접 라우팅** (VPC 내 자동 접근) |
| 사내 커스텀 미러 | Nexus / Artifactory / createrepo | **VPC Lattice** 또는 **PrivateLink** |

---

## 2. 방법별 설명

### 2.1 Amazon Linux 2 / AL2023 — S3 Gateway Endpoint

**왜 S3인가?**

Amazon Linux 패키지 저장소(`cdn.amazonlinux.com`)는 CloudFront → S3로 서빙된다.
S3 Gateway Endpoint를 Private Subnet 라우팅 테이블에 추가하면, EC2가 인터넷 없이도 `dnf install` 이 동작한다.

```
Private EC2
    │
    ▼ (S3 prefix list 라우팅 자동 적용)
S3 Gateway Endpoint (무료, 라우팅 테이블 기반)
    │
    ▼
s3.ap-northeast-2.amazonaws.com
    │
    ▼
amazonlinux-2023 버킷 (ap-northeast-2 리전 미러)
```

**Terraform**

```hcl
# S3 Gateway Endpoint 생성
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.ap-northeast-2.s3"
  vpc_endpoint_type = "Gateway"

  route_table_ids = [
    aws_route_table.private_az1.id,
    aws_route_table.private_az2.id,
  ]

  tags = { Name = "s3-gateway-endpoint" }
}
```

**확인**

```bash
# Private EC2에서 테스트
curl -I https://cdn.amazonlinux.com/  # 응답 오면 OK

# DNF 저장소 캐시 갱신
sudo dnf clean all
sudo dnf makecache

# 정상 설치 확인
sudo dnf install -y jq
```

> **주의:** S3 Gateway Endpoint는 *같은 리전* S3만 지원한다.
> `cdn.amazonlinux.com` 이 다른 리전 S3로 resolve되면 NAT가 필요하다 — 리전 미러가 자동으로 선택되므로 일반적으로 문제없다.

---

### 2.2 RHEL — AWS RHUI (Red Hat Update Infrastructure)

AWS Marketplace에서 런칭한 RHEL 인스턴스에는 **AWS-hosted RHUI 서버**가 자동으로 구성된다.
RHUI 서버는 리전 내 AWS IP 대역 안에 있어 Private Subnet에서도 직접 통신 가능하다.

```bash
# RHUI 저장소 확인
cat /etc/yum.repos.d/redhat-rhui.repo

# 예시 출력
[rhui-REGION-rhel-server-releases]
name=Red Hat Enterprise Linux Server 8 (RPMs)
baseurl=https://rhui3.REGION.aws.ce.redhat.com/pulp/repos/...
enabled=1
```

**Private Subnet에서 RHUI 통신 흐름**

```
Private RHEL EC2
    │
    ▼ (RHUI 서버 IP: 리전 내 AWS IP 대역)
Security Group: outbound 443 허용 (0.0.0.0/0 또는 RHUI IP 대역)
    │
    ▼
rhui3.ap-northeast-2.aws.ce.redhat.com (AWS-managed)
    │
    ▼
Red Hat CDN (콘텐츠는 AWS 내부 캐시에서 서빙)
```

**SG 규칙 (Private RHEL EC2)**

| 방향 | 포트 | 대상 | 목적 |
|------|------|------|------|
| Outbound | 443/tcp | 0.0.0.0/0 (또는 RHUI IP) | RHUI HTTPS 통신 |
| Outbound | 80/tcp | 0.0.0.0/0 | RHUI HTTP (일부 메타데이터) |

> RHUI 서버 IP는 고정이 아니므로 SG에서 IP를 직접 지정하기보다 0.0.0.0/0 outbound에서 443만 허용하거나,
> RHUI 도메인을 Squid 프록시로 허용화이트리스트 처리하는 방식이 권장된다.

**RHUI 접속 불가 시 트러블슈팅**

```bash
# RHUI 서버 도달 가능성 확인
curl -v https://rhui3.ap-northeast-2.aws.ce.redhat.com

# 인증서 확인 (RHUI 클라이언트 인증서 만료 여부)
openssl x509 -in /etc/pki/rhui/product/content.crt -noout -dates

# RHUI 패키지 갱신 (인증서 만료 시)
sudo dnf install -y rh-amazon-rhui-client
```

---

### 2.3 커스텀 내부 미러 — VPC Lattice 활용

여러 VPC / 계정에 걸쳐 **사내 패키지 미러 서버**(Nexus Repository, Artifactory, createrepo)를 공유할 때
**Amazon VPC Lattice**를 활용하면 VPC Peering이나 복잡한 PrivateLink 없이 서비스를 노출할 수 있다.

**아키텍처**

```
┌──────────────── Shared Services VPC ─────────────────┐
│                                                        │
│  Nexus Repo Server (EC2/ECS)                          │
│  - dnf 미러 (AL2023, RHEL)                            │
│  - 포트 8081 (HTTP)                                   │
│        │                                              │
│  [ VPC Lattice Target Group ]                         │
│  [ VPC Lattice Service: repo.internal ]               │
│                                                        │
└──────────────────────────────────────────────────────┘
           │ Service Network Association
           │
    ┌──────┴────────────────────────────────────┐
    │                                           │
┌───┴──── App VPC A ─────┐     ┌──── App VPC B ┴──────┐
│ Private EC2             │     │ Private EC2           │
│ dnf → repo.internal     │     │ dnf → repo.internal   │
└─────────────────────────┘     └──────────────────────┘
```

**Terraform — VPC Lattice Service 구성**

```hcl
# 1. Lattice Service Network 생성
resource "aws_vpclattice_service_network" "pkg_network" {
  name      = "pkg-mirror-network"
  auth_type = "NONE"  # 내부망이면 NONE, 외부 노출 시 AWS_IAM
}

# 2. Lattice Service 생성
resource "aws_vpclattice_service" "nexus_repo" {
  name = "nexus-pkg-mirror"
}

# 3. Target Group (Nexus 서버 EC2)
resource "aws_vpclattice_target_group" "nexus" {
  name = "nexus-tg"
  type = "INSTANCE"

  config {
    port             = 8081
    protocol         = "HTTP"
    vpc_identifier   = aws_vpc.shared_services.id
    health_check {
      enabled             = true
      path                = "/service/rest/v1/status"
      protocol            = "HTTP"
      healthy_threshold   = 3
      unhealthy_threshold = 2
    }
  }
}

# 4. Listener & Rule
resource "aws_vpclattice_listener" "nexus" {
  service_identifier = aws_vpclattice_service.nexus_repo.id
  name               = "http-8081"
  protocol           = "HTTP"
  port               = 8081

  default_action {
    forward {
      target_groups {
        target_group_identifier = aws_vpclattice_target_group.nexus.id
        weight                  = 100
      }
    }
  }
}

# 5. Service Network ↔ Service 연결
resource "aws_vpclattice_service_network_service_association" "nexus" {
  service_identifier         = aws_vpclattice_service.nexus_repo.id
  service_network_identifier = aws_vpclattice_service_network.pkg_network.id
}

# 6. Service Network ↔ VPC 연결 (App VPC마다 반복)
resource "aws_vpclattice_service_network_vpc_association" "app_vpc_a" {
  service_network_identifier = aws_vpclattice_service_network.pkg_network.id
  vpc_identifier             = aws_vpc.app_a.id
  security_group_ids         = [aws_security_group.lattice_sg.id]
}
```

**App VPC EC2의 dnf 저장소 설정**

```bash
# /etc/yum.repos.d/internal-mirror.repo
[internal-nexus-al2023]
name=Internal Nexus Mirror - Amazon Linux 2023
baseurl=http://nexus-pkg-mirror.LATTICE_DNS/repository/al2023-proxy/
enabled=1
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-amazon-linux-2023
```

```bash
# Lattice DNS 확인 (VPC 연결 후 자동 생성되는 도메인)
aws vpc-lattice list-services --query 'items[].dnsEntry'
# 출력: nexus-pkg-mirror-xxxx.ap-northeast-2.vpclattice.aws

# 저장소 갱신
sudo dnf clean all && sudo dnf makecache
sudo dnf install -y jq curl wget
```

---

## 3. 방법 비교 및 선택 가이드

| 시나리오 | 권장 방법 | 이유 |
|----------|----------|------|
| Amazon Linux 2 / AL2023, 단일 VPC | S3 Gateway Endpoint | 무료, 설정 간단 |
| RHEL, AWS Marketplace 런칭 | RHUI (별도 설정 불필요) | 자동 구성, AWS 관리형 |
| RHEL, 온프레미스에서 이관한 인스턴스 | RHUI + rh-amazon-rhui-client 설치 | 사설 레지스트리 등록 필요 |
| 다수 VPC/계정에 사내 미러 공유 | VPC Lattice | VPC Peering 없이 서비스 메시 구성 |
| 엄격한 보안 격리 + 감사 필요 | Squid 프록시 + 허용 도메인 화이트리스트 | 트래픽 검사 가능 |

---

## 4. 공통 트러블슈팅

### 4.1 dnf makecache 타임아웃

```bash
# 증상
Error: Failed to download metadata for repo 'amazonlinux'
Curl error (28): Timeout ...

# 원인 확인: S3 Endpoint 라우팅 누락
aws ec2 describe-route-tables \
  --filters "Name=association.subnet-id,Values=<subnet-id>" \
  --query 'RouteTables[].Routes[?DestinationPrefixListId!=null]'

# 조치: S3 Endpoint를 Private 라우팅 테이블에 연결했는지 확인
```

### 4.2 RHUI 인증서 오류

```bash
# 증상
SSL certificate problem: certificate has expired

# 조치
sudo dnf install -y rh-amazon-rhui-client  # 클라이언트 재설치로 인증서 갱신
sudo dnf clean all && sudo dnf makecache
```

### 4.3 VPC Lattice 도메인 해석 실패

```bash
# 증상
Could not resolve host: nexus-pkg-mirror-xxxx.ap-northeast-2.vpclattice.aws

# 원인: VPC의 enableDnsSupport / enableDnsHostnames 비활성화
aws ec2 modify-vpc-attribute \
  --vpc-id vpc-xxxx \
  --enable-dns-support

aws ec2 modify-vpc-attribute \
  --vpc-id vpc-xxxx \
  --enable-dns-hostnames

# VPC Lattice 연결 상태 확인
aws vpc-lattice list-service-network-vpc-associations \
  --service-network-identifier <sn-id>
```

### 4.4 NAT 없이 패키지 설치 안 되는 경우 최종 점검표

```
□ S3 Gateway Endpoint가 Private Subnet 라우팅 테이블에 연결되어 있는가?
□ Security Group outbound 443 허용되어 있는가?
□ VPC DNS (enableDnsSupport) 활성화 되어 있는가?
□ RHEL의 경우 /etc/yum.repos.d/ 안에 RHUI repo 파일이 존재하는가?
□ VPC Lattice 사용 시 Service Network ↔ VPC Association 상태가 ACTIVE인가?
```

---

## 5. CloudWatch 모니터링

| 지표 | 확인 방법 | 알람 기준 |
|------|----------|----------|
| VPC Lattice 서비스 응답 오류 | `AWS/VPCLattice` > `RequestCount`, `HTTPCode_Target_5XX` | 5XX > 5/min |
| Lattice 대상 상태 | `HealthyHostCount` < 1 | 즉시 알람 |
| RHUI 연결 실패 | CloudWatch Agent + `/var/log/dnf.log` Log Group 수집 | "Error" 패턴 알람 |
| NAT GW 데이터 처리량 감소 | `NatGatewayBytesOutToSource` | S3 Endpoint 적용 후 감소 확인 |

```bash
# /var/log/dnf.log를 CWAgent로 수집하는 설정 (발췌)
{
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [
          {
            "file_path": "/var/log/dnf.log",
            "log_group_name": "/ec2/{instance-id}/dnf",
            "log_stream_name": "{hostname}",
            "timezone": "UTC"
          }
        ]
      }
    }
  }
}
```
