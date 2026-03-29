# aws-basic

AWS 운영 경험을 바탕으로 정리한 실무 지식 베이스입니다.
실제 장애 대응, 트러블슈팅, 베스트 프랙티스를 중심으로 작성되었습니다.

---

## 카테고리

### 보안 (Security)
- [AWS 자격 증명 (Credentials)](aws-credentials.md) — 우선순위, IAM Role 가정, SSO, .gitignore
- [IMDS v1 vs v2](aws-security-imds.md) — SSRF 방어, 컨테이너 hop limit, 401 해결
- [SHA-256 활용](aws-security-sha256-usage.md) — Lambda hash, S3 체크섬, Signature V4
- [IAM Permission Boundary](iam-permission-boundary.md) — 권한 에스컬레이션 방지, SCP 조합, 개발팀 위임 패턴
- [CloudTrail 보안 감사 자동화](cloudtrail-security-audit.md) — Metric Filter 알람, Athena 쿼리, EventBridge 연동

### Direct Connect (DX)
- [DX 로케이션 & 물리 연결](dx-location.md) — Colocation, LOA-CFA, Cross-Connect
- [DX 모니터링](dx-monitoring.md) — 광레벨, ConnectionState, VIF 지표
- [DX 패킷 손실 분석](dx-packet-loss.md) — 대역폭 포화, microburst, 물리 오류
- [BGP & VIF Down 시나리오](dx-bgp-vif-down-scenario.md) — 영향 분석, 책임 범위 정리
- [Resiliency 구성](dx-building-resiliency.md) — High/Maximum 모델, VPN 백업

### EC2
- [Amazon Linux 2 vs AL2023](ec2-al2-al2023.md) — dnf, SELinux, SSH RSA 차단 이슈
- [ASG 내 Stop/Start](ec2-autoscaling-stop-start.md) — Standby 상태, 프로세스 중단
- [Dedicated Instance](ec2-dedicated-instance.md) — 단일 테넌트, Dedicated Host 차이, 비용
- [GPU 텔레메트리 수집](ec2-gpu-telemetry-capturing.md) — NVIDIA DCGM, Xid 오류, CloudWatch
- [물리 호스트 교체](ec2-physical-host-change.md) — Stop&Start 원리, 인스턴스 스토어 주의
- [Spot 인스턴스 운영](ec2-spot-instance.md) — 중단 알림, Mixed Instances Policy, Node Termination Handler
- [EBS 성능 최적화](ec2-ebs-performance.md) — gp2→gp3 마이그레이션, BurstBalance, fio 벤치마크
- [SSM Session Manager](ec2-ssm-session-manager.md) — SSH 대체, VPC 엔드포인트, 포트 포워딩
- [UserData / cloud-init](ec2-userdata-cloud-init.md) — 실행 단계, bash vs cloud-config, 디버깅
- [Launch Template](ec2-launch-template.md) — vs Launch Configuration, 버전 관리, ASG/EKS 연동
- [Placement Group](ec2-placement-group.md) — Cluster/Spread/Partition 전략, EFA 조합
- [AMI 관리 (Golden AMI)](ec2-ami-management.md) — Packer, EC2 Image Builder, 미사용 AMI 정리
- [Enhanced Networking (ENA)](ec2-enhanced-networking.md) — SR-IOV, 기준/버스트 대역폭, iperf3
- [EC2 인스턴스 타입 선택](ec2-instance-types.md) — 패밀리 개요, Graviton ARM64, 멀티아치 빌드

### EKS
- [ImagePullPolicy](eks-imagepullpolicy.md) — Always/IfNotPresent/Never, ECR 인증, ImagePullBackOff
- [Karpenter vs Cluster Autoscaler](eks-karpenter-vs-cluster-autoscaler.md) — 아키텍처 비교, NodePool, 마이그레이션
- [IRSA (IAM Roles for Service Accounts)](eks-irsa.md) — OIDC Provider, Trust Policy, AssumeRoleWithWebIdentity 트러블슈팅
- [VPC CNI 네트워킹](eks-networking-vpc-cni.md) — ENI/IP 한도, Prefix Delegation, IP 고갈 해결
- [클러스터 업그레이드 전략](eks-upgrade-strategy.md) — 버전 정책, 업그레이드 순서, deprecated API 탐지
- [CoreDNS 튜닝](eks-coredns-tuning.md) — ndots:5 문제, NodeLocal DNSCache, Corefile 커스터마이징
- [PersistentVolume (EBS/EFS CSI)](eks-persistent-volume.md) — StorageClass gp3, StatefulSet, VolumeSnapshot
- [HPA / VPA / KEDA](eks-hpa-vpa.md) — 스케일링 공식, VPA 모드, KEDA SQS 연동
- [Node Drain & Cordon](eks-node-drain-cordon.md) — drain 흐름, PDB, preStop 훅, 강제 drain 위험성
- [Secrets 관리 (ESO)](eks-secrets-management.md) — External Secrets Operator, ClusterSecretStore, IRSA 연동
- [Pod Security (PSA / Falco)](eks-pod-security.md) — PSA 3단계, SecurityContext, Falco 런타임 탐지
- [Network Policy](eks-network-policy.md) — Default-deny 패턴, namespace selector AND/OR, DNS egress 허용
- [Managed Node Group](eks-managed-nodegroup.md) — Managed vs Self-managed, Karpenter 역할 분리, taint/label
- [Resource Requests & Limits](eks-resource-requests-limits.md) — QoS 클래스, OOMKilled/CPU throttling, LimitRange, ResourceQuota

### CloudWatch / 모니터링
- [커스텀 지표 수집](cloudwatch-custom-metric.md) — Agent config, put-metric-data, GPU 지표 스크립트
- [Fluent Bit on EKS](cloudwatch-eks-fluentbit.md) — DaemonSet, IRSA, 멀티라인 파싱

### 네트워크 / 로드밸런서
- [NLB 포트 포워딩](nlb-ec2-port-forwarding.md) — Terraform, 헬스체크, SG 트러블슈팅
- [VPC Flow Logs 분석](vpc-flow-logs-analysis.md) — 레코드 필드, Athena DDL, 보안 감사 쿼리
- [Route 53 Failover 라우팅](route53-failover-routing.md) — 헬스체크 유형, TTL 설정, Active-Passive 패턴
- [VPC & Subnet 설계 전략](vpc-subnet-design.md) — CIDR 크기 기준, 3계층 설계, IP 고갈 대응, Secondary CIDR
- [VPC Endpoint](vpc-endpoint.md) — Gateway/Interface 비교, ECR/SSM/S3 Endpoint, NAT GW 비용 절감
- [Transit Gateway](aws-transit-gateway.md) — Hub-and-Spoke, 환경 격리, 멀티 계정 공유, VPN 연결

### 스토리지
- [S3 스토리지 클래스 & Lifecycle](s3-lifecycle-intelligent-tiering.md) — 클래스 비교, Intelligent-Tiering, 비용 최적화
- [ECR 이미지 관리 & Lifecycle](ecr-lifecycle-policy.md) — 이미지 태그 전략, Lifecycle Policy, 취약점 스캔

### 데이터베이스 / 캐시
- [RDS 파라미터 그룹 튜닝](rds-parameter-group.md) — MySQL/PostgreSQL 파라미터, 슬로우 쿼리, 연결 관리
- [Aurora 클러스터 운영](rds-aurora-cluster.md) — Writer/Reader 엔드포인트, 페일오버, Auto Scaling, Clone
- [ElastiCache Redis 클러스터](elasticache-redis-cluster.md) — 클러스터 모드, maxmemory-policy, 페일오버, Eviction

### 비용 최적화 / 거버넌스
- [AWS 비용 최적화](aws-cost-optimization.md) — Savings Plans vs RI vs Spot, 미사용 리소스 탐지, Budget 알람
- [AWS Organizations 멀티 계정](aws-organizations-multi-account.md) — OU 구조, SCP 가드레일, SSO, 멀티 계정 전략

### 보안 / 방어
- [WAF 규칙 & Rate Limiting](waf-rate-limiting.md) — Managed Rule Group, Rate-based Rule, False Positive 대응

---

> 문서 작성 규칙 및 템플릿은 [CLAUDE.md](CLAUDE.md)를 참고하세요.
