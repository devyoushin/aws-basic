# CLAUDE.md — aws-basic 지식 베이스

AWS 운영 경험 기반의 개인 지식 베이스입니다. 문서 추가/수정 시 아래 가이드를 따릅니다.

---

## 프로젝트 구조

```
aws-basic/
├── docs/                          # 지식 문서 (카테고리별 분류)
│   ├── ec2/         (18개)        # EC2, EBS, AMI, ASG
│   ├── eks/         (16개)        # EKS, Karpenter, IRSA, CoreDNS
│   ├── cloudwatch/  (13개)        # 지표, 알람, 로그, 대시보드
│   ├── network/     (6개)         # VPC, NLB, Route53, TGW
│   ├── security/    (6개)         # IAM, CloudTrail, WAF, IMDS
│   ├── storage/     (3개)         # S3, ECR, EFS
│   ├── database/    (3개)         # RDS, Aurora, ElastiCache
│   ├── dx/          (5개)         # Direct Connect, BGP, VIF
│   ├── cost/        (2개)         # 비용 최적화, Organizations
│   └── platform/    (2개)         # CLI, CodeDeploy
│
├── cli/                           # AWS CLI 스크립트 (8개)
├── sdk/                           # Python boto3 모듈 (8개)
├── lambda/                        # Lambda 함수 예제 (8개)
│
├── templates/                     # 재사용 문서 템플릿
│   ├── service-doc.md             # 서비스 문서 스캐폴딩
│   ├── runbook.md                 # 운영 Runbook
│   └── incident-report.md        # 장애 보고서
│
├── rules/                         # Claude 작성 규칙
│   ├── doc-writing.md             # 문서 스타일 가이드
│   ├── aws-conventions.md         # CLI/Terraform/boto3 코드 규칙
│   ├── security-checklist.md      # 보안 검토 체크리스트
│   └── monitoring.md              # 모니터링/알람 작성 기준
│
├── agents/                        # Claude 전문 에이전트
│   ├── doc-writer.md              # 문서 작성 에이전트
│   ├── incident-analyzer.md       # 장애 분석 에이전트
│   ├── cost-reviewer.md           # 비용 최적화 에이전트
│   └── architecture-advisor.md    # 아키텍처 설계/검토 에이전트
│
└── .claude/
    ├── settings.json              # 프로젝트 공유 설정
    └── commands/                  # 커스텀 슬래시 커맨드
        ├── new-doc.md             # /new-doc
        ├── new-runbook.md         # /new-runbook
        ├── review-doc.md          # /review-doc
        ├── add-troubleshooting.md # /add-troubleshooting
        └── search-kb.md           # /search-kb
```

---

## 커스텀 슬래시 커맨드

| 커맨드 | 사용법 | 설명 |
|--------|--------|------|
| `/new-doc` | `/new-doc ec2 nitro-system` | 신규 서비스 문서 스캐폴딩 |
| `/new-runbook` | `/new-runbook rds failover` | 운영 Runbook 생성 |
| `/review-doc` | `/review-doc docs/eks/eks-irsa.md` | 문서 품질 검토 |
| `/add-troubleshooting` | `/add-troubleshooting docs/ec2/ec2-ebs-performance.md <증상>` | 트러블슈팅 추가 |
| `/search-kb` | `/search-kb IRSA 토큰` | 지식 베이스 키워드 검색 |

---

## 파일 네이밍 규칙

```
docs/{카테고리}/{서비스}-{주제}.md
```

- 서비스 약어: `ec2`, `eks`, `dx`, `cloudwatch`, `nlb`, `vpc`, `s3`, `iam`, `route53`, `rds`
- 주제: 소문자 영어, 하이픈 구분
- 예시: `docs/ec2/ec2-spot-interruption.md`, `docs/eks/eks-fargate-logging.md`

---

## 문서 작성 원칙

1. **실제 경험 기반** — 운영 중 실제로 겪은 이슈와 해결 방법 위주
2. **재현 가능한 코드** — Terraform, AWS CLI 복붙 즉시 실행 가능 수준
3. **원인 중심 트러블슈팅** — 증상만 나열하지 말고 근본 원인 설명
4. **한국어 기술 문서** — 주요 개념은 영어 원문 병기
5. **모니터링 필수** — 모든 문서에 CloudWatch 지표/알람 포함

세부 규칙은 `rules/` 디렉토리를 참조합니다.

---

## 카테고리별 문서 목록

### docs/ec2/
| 파일 | 주제 |
|------|------|
| `ec2-al2-al2023.md` | Amazon Linux 2 vs AL2023 (패키지 매니저, SELinux, SSH) |
| `ec2-al2023-migration.md` | AL2 → AL2023 마이그레이션 IP 유지 (ENI Swap, EIP, NLB Target 교체) |
| `ec2-ami-management.md` | Golden AMI 파이프라인, Packer, EC2 Image Builder |
| `ec2-autoscaling-stop-start.md` | ASG 내 인스턴스 Stop/Start, Standby 상태 활용 |
| `ec2-dedicated-instance.md` | Dedicated Instance vs Dedicated Host, 비용/라이선스 |
| `ec2-ebs-performance.md` | EBS gp2→gp3 마이그레이션, BurstBalance, fio 벤치마크 |
| `ec2-enhanced-networking.md` | ENA SR-IOV, 기준/버스트 대역폭, iperf3 테스트 |
| `ec2-gpu-telemetry-capturing.md` | GPU 텔레메트리 수집, NVIDIA DCGM, Xid 오류 코드 |
| `ec2-instance-types.md` | 인스턴스 패밀리 개요, Graviton ARM64, 멀티아치 Docker 빌드 |
| `ec2-launch-template.md` | Launch Template 버전 관리, ASG/EKS Managed NodeGroup 연동 |
| `ec2-physical-host-change.md` | 물리 호스트 교체 (Stop&Start), 인스턴스 스토어 주의사항 |
| `ec2-placement-group.md` | Cluster/Spread/Partition 전략, EFA 조합, 용량 예약 |
| `ec2-rolling-maintenance.md` | Target Group 연동 EC2 롤링 PM 작업, AZ별 순차 재기동 |
| `ec2-snapshot-root-volume-recovery.md` | EBS 스냅샷 생성, 루트 볼륨 복구, DLM 자동화 |
| `ec2-spot-instance.md` | Spot 인스턴스 운영, 중단 알림 처리, Node Termination Handler |
| `ec2-ssm-session-manager.md` | SSM Session Manager (SSH 대체), VPC 엔드포인트 구성 |
| `ec2-userdata-cloud-init.md` | UserData/cloud-init 실행 단계, bash vs cloud-config 디버깅 |
| `ec2-rhel-upgrade.md` | RHEL dnf 마이너 버전 업그레이드, RHEL 8→9 Leapp 메이저 업그레이드 |

### docs/eks/
| 파일 | 주제 |
|------|------|
| `eks-coredns-tuning.md` | CoreDNS ndots:5 문제, NodeLocal DNSCache, Corefile 커스터마이징 |
| `eks-hpa-vpa.md` | HPA/VPA 설정, KEDA SQS 연동, 충돌 방지 패턴 |
| `eks-imagepullpolicy.md` | ImagePullPolicy 3가지, ECR 인증, ImagePullBackOff 해결 |
| `eks-irsa.md` | IRSA (OIDC Provider, Trust Policy, SA annotation, Cross-account) |
| `eks-karpenter-vs-cluster-autoscaler.md` | Karpenter vs CA 아키텍처 비교, NodePool 설정, 마이그레이션 |
| `eks-managed-nodegroup.md` | Managed vs Self-managed, Karpenter 역할 분리, taint/label |
| `eks-network-policy.md` | NetworkPolicy Default-deny, namespace selector, DNS egress |
| `eks-networking-vpc-cni.md` | VPC CNI ENI/IP 한도, Prefix Delegation, IP 고갈 해결 |
| `eks-node-drain-cordon.md` | Node Drain/Cordon, PDB, preStop 훅, 안전한 노드 교체 |
| `eks-persistent-volume.md` | EBS/EFS CSI Driver, StorageClass gp3, StatefulSet, VolumeSnapshot |
| `eks-pod-security.md` | Pod Security Admission 3단계, Falco 런타임 탐지 |
| `eks-resource-requests-limits.md` | QoS 클래스, OOMKilled/CPU throttling, LimitRange, ResourceQuota |
| `eks-secrets-management.md` | External Secrets Operator, ClusterSecretStore, IRSA 연동 |
| `eks-upgrade-strategy.md` | EKS 클러스터 업그레이드 전략, deprecated API 탐지 |
| `eks-eip-ip-strategy.md` | EKS 노드 EIP 할당 한도, IP 전략 4가지 패턴 (NAT/EIP/Pod EIP/IPv6) |
| `eks-cilium-cni.md` | VPC CNI → Cilium 마이그레이션 전략, eBPF, Hubble, L7 정책 |
| `eks-karpenter-iam-deep-dive.md` | Karpenter IAM 권한 획득 원리, IRSA 토큰 흐름, /var/run, 노드 프로비저닝 |

### docs/cloudwatch/
| 파일 | 주제 |
|------|------|
| `cloudwatch-agent-config.md` | CWAgent 설정 (메모리/디스크/procstat), EC2/EKS DaemonSet 배포 |
| `cloudwatch-alarm-composite.md` | Composite Alarm AND/OR 조합, Alarm Storm 방지, 계층형 알람 |
| `cloudwatch-container-insights.md` | Container Insights EKS/ECS 지표, 성능 패널, 비용 최적화 |
| `cloudwatch-cross-account.md` | OAM 크로스 계정 지표/로그, Sink/Link, Organizations 통합 |
| `cloudwatch-custom-metric.md` | 커스텀 지표 수집 (Agent, SDK, CLI), GPU 지표 스크립트 |
| `cloudwatch-dashboard-best-practice.md` | 대시보드 설계 원칙, USE/RED 메서드, Variable, 위젯 패턴 |
| `cloudwatch-eks-fluentbit.md` | Fluent Bit on EKS, DaemonSet 구성, 멀티라인 파싱 |
| `cloudwatch-embedded-metrics.md` | EMF 구조화 지표, Lambda/컨테이너 로그 기반 지표 발행 |
| `cloudwatch-evidently.md` | Feature Flag, A/B 테스트, Launch/Experiment, Kill Switch |
| `cloudwatch-log-insights.md` | Logs Insights 쿼리 문법, 집계/파싱, 대시보드 연동 |
| `cloudwatch-metric-math.md` | Metric Math 수식, 에러율/포화도 계산, ANOMALY_DETECTION |
| `cloudwatch-rum.md` | RUM 프론트엔드 성능, Core Web Vitals, 커스텀 이벤트 |
| `cloudwatch-synthetics.md` | Synthetics Canary API/UI 외부 모니터링, Puppeteer 스크립트 |

### docs/network/
| 파일 | 주제 |
|------|------|
| `aws-transit-gateway.md` | Hub-and-Spoke 멀티 VPC, 환경 격리 라우팅, RAM 공유 |
| `nlb-ec2-port-forwarding.md` | NLB 포트 포워딩, Terraform 코드, 헬스체크 트러블슈팅 |
| `route53-failover-routing.md` | Route 53 Failover 라우팅, 헬스체크 유형, Active-Passive |
| `vpc-endpoint.md` | Gateway/Interface Endpoint, ECR/SSM 비용 절감, Private DNS |
| `vpc-flow-logs-analysis.md` | VPC Flow Logs 분석, Athena DDL, 보안 감사 쿼리 패턴 |
| `vpc-subnet-design.md` | VPC/Subnet CIDR 설계, 3계층 구조, IP 고갈 대응, Secondary CIDR |
| `vpc-private-dnf-repo.md` | Private Subnet DNF/YUM 패키지 설치 (S3 Endpoint, RHUI, VPC Lattice 미러) |

### docs/security/
| 파일 | 주제 |
|------|------|
| `aws-credentials.md` | AWS 자격 증명 우선순위, IAM Role 가정, SSO 활용 |
| `aws-security-imds.md` | IMDSv1 vs IMDSv2 비교, SSRF 방어, 컨테이너 hop limit |
| `aws-security-sha256-usage.md` | SHA-256 활용 (Lambda hash, S3 체크섬, Sig V4) |
| `cloudtrail-security-audit.md` | CloudTrail Trail 구성, 보안 이벤트 알람, Athena 감사 쿼리 |
| `iam-permission-boundary.md` | Permission Boundary 설계, 권한 에스컬레이션 방지, SCP 강제 |
| `waf-rate-limiting.md` | WAF Managed Rules, Rate-based Rule, False Positive 대응 |

### docs/storage/
| 파일 | 주제 |
|------|------|
| `ecr-lifecycle-policy.md` | ECR 이미지 태그 전략, Lifecycle Policy, 취약점 스캔 자동화 |
| `efs-access-point.md` | EFS Access Point, 다중 테넌트 격리, 마운트 설정 |
| `s3-lifecycle-intelligent-tiering.md` | S3 스토리지 클래스 비교, Lifecycle 자동화, Intelligent-Tiering |

### docs/database/
| 파일 | 주제 |
|------|------|
| `elasticache-redis-cluster.md` | Redis 클러스터 모드, 메모리 정책, 페일오버, Eviction |
| `rds-aurora-cluster.md` | Aurora Writer/Reader, 페일오버, Auto Scaling, 클론 |
| `rds-parameter-group.md` | MySQL/PostgreSQL 파라미터 튜닝, 슬로우 쿼리 로깅, 연결 관리 |

### docs/dx/
| 파일 | 주제 |
|------|------|
| `dx-bgp-vif-down-scenario.md` | BGP/VIF Down 시나리오, 영향 분석, 책임 범위 |
| `dx-building-resiliency.md` | High/Maximum Resiliency 모델, VPN 백업 구성 |
| `dx-location.md` | DX 로케이션 개념, Cross-Connect, LOA-CFA |
| `dx-monitoring.md` | CloudWatch DX 지표 (ConnectionState, 광레벨, VIF) |
| `dx-packet-loss.md` | 패킷 손실 원인 분석 (대역폭 포화, microburst, 물리 오류) |

### docs/cost/
| 파일 | 주제 |
|------|------|
| `aws-cost-optimization.md` | Savings Plans/RI/Spot 전략, 미사용 리소스 탐지, Budget 알람 |
| `aws-organizations-multi-account.md` | OU 구조, SCP 가드레일, IAM Identity Center SSO, 멀티 계정 설계 |

### docs/platform/
| 파일 | 주제 |
|------|------|
| `aws-cli-internals.md` | AWS CLI 동작 원리 (Python/botocore 구조, SigV4, 페이지네이션, v1 vs v2) |
| `aws-codedeploy.md` | CodeDeploy In-Place/Blue-Green 배포, AppSpec Lifecycle Hook, Terraform, 롤백 |
| `landing-zone-architecture.md` | Enterprise Landing Zone 구성도 (OU 계층, 데이터 흐름, 모니터링 알람 계층) |

---

## 추가 예정 주제 (백로그)

- `docs/eks/eks-fargate-logging.md` — Fargate 환경 로깅 (Fluent Bit sidecar)
- `docs/platform/lambda-best-practices.md` — Lambda 콜드 스타트, 레이어, 동시성 제한
- `docs/platform/sqs-dlq-pattern.md` — SQS Dead Letter Queue, 메시지 재처리 패턴
- `docs/cost/aws-backup.md` — AWS Backup 중앙화, Cross-Region 복사, 규정 준수
- `docs/network/alb-advanced.md` — ALB 고급 라우팅, Listener Rule, Target Group 전략
