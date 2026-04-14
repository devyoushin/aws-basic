# CLAUDE.md — aws-basic 지식 베이스 가이드

## 저장소 목적

AWS 운영 경험을 바탕으로 실제 업무에서 겪은 이슈, 트러블슈팅, 베스트 프랙티스를 정리한 개인 지식 베이스입니다.
새 문서 추가나 기존 문서 보완 시 아래 가이드를 따릅니다.

---

## 파일 네이밍 규칙

```
{서비스}-{주제}.md
```

- 서비스 약어: `ec2`, `eks`, `dx` (Direct Connect), `cloudwatch`, `nlb`, `vpc`, `s3`, `iam`, `route53`, `aws` (범용 AWS)
- 주제는 소문자 영어, 단어 구분은 하이픈(`-`)
- 예시: `ec2-spot-interruption.md`, `eks-fargate-logging.md`, `s3-lifecycle-policy.md`

---

## 문서 구조 템플릿

새 문서를 추가할 때 아래 구조를 따릅니다:

```markdown
# {주제명}

## 1. 개요
- 이 기술/기능이 무엇인지 1~3문장으로 설명
- 왜 알아야 하는지 (운영 상 의미)

## 2. 설명
### 2.1 핵심 개념
- 동작 원리, 주요 차이점, 아키텍처

### 2.2 실무 적용 코드
- Terraform HCL, AWS CLI, YAML 등 실제 사용 코드 포함

### 2.3 보안/비용 Best Practice
- 운영 시 주의해야 할 보안 설정, 비용 절감 포인트

## 3. 트러블슈팅
### 3.1 주요 이슈
- 증상 → 원인 → 해결 방법 순으로 작성

### 3.2 자주 발생하는 문제 (Q&A)
- Q: 문제 상황
- A: 해결 방법

## 4. 모니터링 및 알람
- 관련 CloudWatch 지표, 알람 설정 예시

## 5. TIP
- 현장에서 유용한 팁, 관련 문서 링크
```

---

## 카테고리별 파일 목록

### 보안 (Security)
| 파일 | 주제 |
|------|------|
| `aws-credentials.md` | AWS 자격 증명 우선순위, IAM Role 가정, SSO 활용 |
| `aws-security-imds.md` | IMDSv1 vs IMDSv2 비교, SSRF 방어, 컨테이너 hop limit |
| `aws-security-sha256-usage.md` | SHA-256 활용 (Lambda hash, S3 체크섬, Sig V4) |

### Direct Connect (DX)
| 파일 | 주제 |
|------|------|
| `dx-location.md` | DX 로케이션 개념, Cross-Connect, LOA-CFA |
| `dx-monitoring.md` | CloudWatch DX 지표 (ConnectionState, 광레벨, VIF) |
| `dx-packet-loss.md` | 패킷 손실 원인 분석 (대역폭 포화, microburst, 물리 오류) |
| `dx-bgp-vif-down-scenario.md` | BGP/VIF Down 시나리오, 영향 분석, 책임 범위 |
| `dx-building-resiliency.md` | High/Maximum Resiliency 모델, VPN 백업 구성 |

### EC2
| 파일 | 주제 |
|------|------|
| `ec2-al2-al2023.md` | Amazon Linux 2 vs AL2023 (패키지 매니저, SELinux, SSH) |
| `ec2-al2023-migration.md` | AL2 → AL2023 마이그레이션 IP 유지 (ENI Swap, EIP, NLB Target 교체) |
| `ec2-autoscaling-stop-start.md` | ASG 내 인스턴스 Stop/Start, Standby 상태 활용 |
| `ec2-dedicated-instance.md` | Dedicated Instance vs Dedicated Host, 비용/라이선스 |
| `ec2-gpu-telemetry-capturing.md` | GPU 텔레메트리 수집, NVIDIA DCGM, Xid 오류 코드 |
| `ec2-physical-host-change.md` | 물리 호스트 교체 (Stop&Start), 인스턴스 스토어 주의사항 |
| `ec2-spot-instance.md` | Spot 인스턴스 운영, 중단 알림 처리, Node Termination Handler |
| `ec2-ebs-performance.md` | EBS gp2→gp3 마이그레이션, BurstBalance, fio 벤치마크 |
| `ec2-ssm-session-manager.md` | SSM Session Manager (SSH 대체), VPC 엔드포인트 구성 |
| `ec2-userdata-cloud-init.md` | UserData/cloud-init 실행 단계, bash vs cloud-config 디버깅 |
| `ec2-launch-template.md` | Launch Template 버전 관리, ASG/EKS Managed NodeGroup 연동 |
| `ec2-placement-group.md` | Cluster/Spread/Partition 전략, EFA 조합, 용량 예약 |
| `ec2-ami-management.md` | Golden AMI 파이프라인, Packer, EC2 Image Builder |
| `ec2-enhanced-networking.md` | ENA SR-IOV, 기준/버스트 대역폭, iperf3 테스트 |
| `ec2-instance-types.md` | 인스턴스 패밀리 개요, Graviton ARM64, 멀티아치 Docker 빌드 |
| `ec2-snapshot-root-volume-recovery.md` | EBS 스냅샷 생성, 루트 볼륨 복구 (Detach/Attach, Replace Root Volume), DLM 자동화 |

### EKS
| 파일 | 주제 |
|------|------|
| `eks-imagepullpolicy.md` | ImagePullPolicy 3가지, ECR 인증, ImagePullBackOff 해결 |
| `eks-karpenter-vs-cluster-autoscaler.md` | Karpenter vs CA 아키텍처 비교, NodePool 설정, 마이그레이션 |
| `eks-irsa.md` | IRSA (OIDC Provider, Trust Policy, SA annotation, 토큰 교환 플로우 심화, Cross-account) |
| `eks-networking-vpc-cni.md` | VPC CNI ENI/IP 한도, Prefix Delegation, IP 고갈 해결 |
| `eks-upgrade-strategy.md` | EKS 클러스터 업그레이드 전략, deprecated API 탐지 |
| `eks-coredns-tuning.md` | CoreDNS ndots:5 문제, NodeLocal DNSCache, Corefile 커스터마이징 |
| `eks-persistent-volume.md` | EBS/EFS CSI Driver, StorageClass gp3, StatefulSet, VolumeSnapshot, EFS 다중 Access Point 격리 |
| `eks-hpa-vpa.md` | HPA/VPA 설정, KEDA SQS 연동, 충돌 방지 패턴 |
| `eks-node-drain-cordon.md` | Node Drain/Cordon, PDB, preStop 훅, 안전한 노드 교체 |
| `eks-secrets-management.md` | External Secrets Operator, ClusterSecretStore, IRSA 연동 |
| `eks-pod-security.md` | Pod Security Admission 3단계, Falco 런타임 탐지 |
| `eks-network-policy.md` | NetworkPolicy Default-deny, namespace selector, DNS egress |
| `eks-managed-nodegroup.md` | Managed vs Self-managed, Karpenter 역할 분리, taint/label |
| `eks-resource-requests-limits.md` | QoS 클래스, OOMKilled/CPU throttling, LimitRange, ResourceQuota |

### CloudWatch / 모니터링
| 파일 | 주제 |
|------|------|
| `cloudwatch-custom-metric.md` | 커스텀 지표 수집 (Agent, SDK, CLI), GPU 지표 스크립트 |
| `cloudwatch-eks-fluentbit.md` | Fluent Bit on EKS, DaemonSet 구성, 멀티라인 파싱 |
| `cloudwatch-container-insights.md` | Container Insights EKS/ECS 지표, 성능 패널, 비용 최적화 |
| `cloudwatch-alarm-composite.md` | Composite Alarm AND/OR 조합, Alarm Storm 방지, 계층형 알람 |
| `cloudwatch-log-insights.md` | Logs Insights 쿼리 문법, 집계/파싱, 대시보드 연동 |
| `cloudwatch-metric-math.md` | Metric Math 수식, 에러율/포화도 계산, ANOMALY_DETECTION |
| `cloudwatch-agent-config.md` | CWAgent 설정 (메모리/디스크/procstat), EC2/EKS DaemonSet 배포 |
| `cloudwatch-synthetics.md` | Synthetics Canary API/UI 외부 모니터링, Puppeteer 스크립트 |
| `cloudwatch-embedded-metrics.md` | EMF 구조화 지표, Lambda/컨테이너 로그 기반 지표 발행 |
| `cloudwatch-cross-account.md` | OAM 크로스 계정 지표/로그, Sink/Link, Organizations 통합 |
| `cloudwatch-dashboard-best-practice.md` | 대시보드 설계 원칙, USE/RED 메서드, Variable, 위젯 패턴 |
| `cloudwatch-rum.md` | RUM 프론트엔드 성능, Core Web Vitals, 커스텀 이벤트 |
| `cloudwatch-evidently.md` | Feature Flag, A/B 테스트, Launch/Experiment, Kill Switch |

### 네트워크 / 로드밸런서
| 파일 | 주제 |
|------|------|
| `nlb-ec2-port-forwarding.md` | NLB 포트 포워딩, Terraform 코드, 헬스체크 트러블슈팅 |
| `vpc-flow-logs-analysis.md` | VPC Flow Logs 분석, Athena DDL, 보안 감사 쿼리 패턴 |
| `route53-failover-routing.md` | Route 53 Failover 라우팅, 헬스체크 유형, Active-Passive |
| `vpc-subnet-design.md` | VPC/Subnet CIDR 설계, 3계층 구조, IP 고갈 대응, Secondary CIDR |
| `vpc-endpoint.md` | Gateway/Interface Endpoint, ECR/SSM 비용 절감, Private DNS |
| `aws-transit-gateway.md` | Hub-and-Spoke 멀티 VPC, 환경 격리 라우팅, RAM 공유 |

### 스토리지
| 파일 | 주제 |
|------|------|
| `s3-lifecycle-intelligent-tiering.md` | S3 스토리지 클래스 비교, Lifecycle 자동화, Intelligent-Tiering |
| `ecr-lifecycle-policy.md` | ECR 이미지 태그 전략, Lifecycle Policy, 취약점 스캔 자동화 |

### 데이터베이스 / 캐시
| 파일 | 주제 |
|------|------|
| `rds-parameter-group.md` | MySQL/PostgreSQL 파라미터 튜닝, 슬로우 쿼리 로깅, 연결 관리 |
| `rds-aurora-cluster.md` | Aurora Writer/Reader, 페일오버, Auto Scaling, 클론 |
| `elasticache-redis-cluster.md` | Redis 클러스터 모드, 메모리 정책, 페일오버, Eviction |

### 배포 (Deploy)
| 파일 | 주제 |
|------|------|
| `aws-codedeploy.md` | CodeDeploy In-Place/Blue-Green 배포, AppSpec Lifecycle Hook, Terraform, 롤백 |

### 비용 / 거버넌스
| 파일 | 주제 |
|------|------|
| `aws-cost-optimization.md` | Savings Plans/RI/Spot 전략, 미사용 리소스 탐지, Budget 알람 |
| `aws-organizations-multi-account.md` | OU 구조, SCP 가드레일, IAM Identity Center SSO, 멀티 계정 설계 |

### 보안 (Security)
| 파일 | 주제 |
|------|------|
| `iam-permission-boundary.md` | Permission Boundary 설계, 권한 에스컬레이션 방지, SCP 강제 |
| `cloudtrail-security-audit.md` | CloudTrail Trail 구성, 보안 이벤트 알람, Athena 감사 쿼리 |
| `waf-rate-limiting.md` | WAF Managed Rules, Rate-based Rule, False Positive 대응 |

---

## 작성 원칙

1. **실제 경험 기반** — 운영 중 실제로 겪은 이슈와 해결 방법 위주로 작성
2. **재현 가능한 코드** — Terraform, AWS CLI 등 복붙해서 바로 쓸 수 있는 수준으로
3. **원인 중심 트러블슈팅** — 증상만 나열하지 말고 왜 발생하는지 설명
4. **한국어 기술 문서** — 주요 개념은 영어 원문을 병기 (예: 가용 영역 (Availability Zone))
5. **모니터링 필수** — 새 주제는 관련 CloudWatch 지표 또는 알람 설정을 반드시 포함

---

## 추가 예정 주제 (백로그)

- `eks-fargate-logging.md` — Fargate 환경 로깅 (Fluent Bit sidecar)
- `lambda-best-practices.md` — Lambda 콜드 스타트, 레이어, 동시성 제한
- `sqs-dlq-pattern.md` — SQS Dead Letter Queue, 메시지 재처리 패턴
- `aws-backup.md` — AWS Backup 중앙화, Cross-Region 복사, 규정 준수
