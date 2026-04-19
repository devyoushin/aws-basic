# aws-basic

AWS 운영 경험을 바탕으로 실제 업무에서 겪은 이슈, 트러블슈팅, 베스트 프랙티스를 정리한 개인 지식 베이스입니다.

---

## 구조

```
aws-basic/
├── docs/          지식 문서 (10개 카테고리, 71개 파일)
├── cli/           AWS CLI 스크립트 (8개)
├── sdk/           Python boto3 모듈 (8개)
├── lambda/        Lambda 함수 예제 (8개)
├── templates/     문서 템플릿 (3개)
├── rules/         Claude 작성 규칙 (4개)
└── agents/        Claude 에이전트 정의 (4개)
```

---

## 문서 목록

### EC2 [`docs/ec2/`](docs/ec2/)

| 파일 | 주제 |
|------|------|
| [ec2-al2-al2023](docs/ec2/ec2-al2-al2023.md) | Amazon Linux 2 vs AL2023 |
| [ec2-al2023-migration](docs/ec2/ec2-al2023-migration.md) | AL2 → AL2023 마이그레이션 IP 유지 |
| [ec2-ami-management](docs/ec2/ec2-ami-management.md) | Golden AMI 파이프라인, Packer, Image Builder |
| [ec2-autoscaling-stop-start](docs/ec2/ec2-autoscaling-stop-start.md) | ASG Stop/Start, Standby 상태 |
| [ec2-dedicated-instance](docs/ec2/ec2-dedicated-instance.md) | Dedicated Instance vs Dedicated Host |
| [ec2-ebs-performance](docs/ec2/ec2-ebs-performance.md) | gp2→gp3 마이그레이션, BurstBalance |
| [ec2-enhanced-networking](docs/ec2/ec2-enhanced-networking.md) | ENA SR-IOV, iperf3 |
| [ec2-gpu-telemetry-capturing](docs/ec2/ec2-gpu-telemetry-capturing.md) | GPU 텔레메트리, NVIDIA DCGM |
| [ec2-instance-types](docs/ec2/ec2-instance-types.md) | 인스턴스 패밀리, Graviton ARM64 |
| [ec2-launch-template](docs/ec2/ec2-launch-template.md) | Launch Template 버전 관리 |
| [ec2-physical-host-change](docs/ec2/ec2-physical-host-change.md) | 물리 호스트 교체, 인스턴스 스토어 |
| [ec2-placement-group](docs/ec2/ec2-placement-group.md) | Cluster/Spread/Partition 전략 |
| [ec2-rolling-maintenance](docs/ec2/ec2-rolling-maintenance.md) | Target Group 연동 롤링 PM 자동화 |
| [ec2-snapshot-root-volume-recovery](docs/ec2/ec2-snapshot-root-volume-recovery.md) | EBS 스냅샷, 루트 볼륨 복구, DLM |
| [ec2-spot-instance](docs/ec2/ec2-spot-instance.md) | Spot 운영, 중단 알림, NTH |
| [ec2-ssm-session-manager](docs/ec2/ec2-ssm-session-manager.md) | SSM Session Manager (SSH 대체) |
| [ec2-userdata-cloud-init](docs/ec2/ec2-userdata-cloud-init.md) | UserData/cloud-init 실행 단계 |

### EKS [`docs/eks/`](docs/eks/)

| 파일 | 주제 |
|------|------|
| [eks-coredns-tuning](docs/eks/eks-coredns-tuning.md) | CoreDNS ndots:5, NodeLocal DNSCache |
| [eks-hpa-vpa](docs/eks/eks-hpa-vpa.md) | HPA/VPA, KEDA SQS 연동 |
| [eks-imagepullpolicy](docs/eks/eks-imagepullpolicy.md) | ImagePullPolicy, ECR 인증, ImagePullBackOff |
| [eks-irsa](docs/eks/eks-irsa.md) | IRSA 토큰 교환 플로우, Cross-account |
| [eks-karpenter-vs-cluster-autoscaler](docs/eks/eks-karpenter-vs-cluster-autoscaler.md) | Karpenter vs CA 비교 |
| [eks-managed-nodegroup](docs/eks/eks-managed-nodegroup.md) | Managed vs Self-managed NodeGroup |
| [eks-network-policy](docs/eks/eks-network-policy.md) | NetworkPolicy Default-deny |
| [eks-networking-vpc-cni](docs/eks/eks-networking-vpc-cni.md) | VPC CNI, Prefix Delegation, IP 고갈 |
| [eks-node-drain-cordon](docs/eks/eks-node-drain-cordon.md) | Drain/Cordon, PDB, preStop 훅 |
| [eks-persistent-volume](docs/eks/eks-persistent-volume.md) | EBS/EFS CSI, gp3 StorageClass |
| [eks-pod-security](docs/eks/eks-pod-security.md) | PSA 3단계, Falco 런타임 탐지 |
| [eks-resource-requests-limits](docs/eks/eks-resource-requests-limits.md) | QoS, OOMKilled, LimitRange |
| [eks-secrets-management](docs/eks/eks-secrets-management.md) | External Secrets Operator, IRSA 연동 |
| [eks-upgrade-strategy](docs/eks/eks-upgrade-strategy.md) | 클러스터 업그레이드, deprecated API |

### CloudWatch [`docs/cloudwatch/`](docs/cloudwatch/)

| 파일 | 주제 |
|------|------|
| [cloudwatch-agent-config](docs/cloudwatch/cloudwatch-agent-config.md) | CWAgent 메모리/디스크/procstat |
| [cloudwatch-alarm-composite](docs/cloudwatch/cloudwatch-alarm-composite.md) | Composite Alarm, Alarm Storm 방지 |
| [cloudwatch-container-insights](docs/cloudwatch/cloudwatch-container-insights.md) | EKS/ECS Container Insights |
| [cloudwatch-cross-account](docs/cloudwatch/cloudwatch-cross-account.md) | OAM 크로스 계정, Sink/Link |
| [cloudwatch-custom-metric](docs/cloudwatch/cloudwatch-custom-metric.md) | 커스텀 지표, GPU 스크립트 |
| [cloudwatch-dashboard-best-practice](docs/cloudwatch/cloudwatch-dashboard-best-practice.md) | USE/RED 메서드, 대시보드 설계 |
| [cloudwatch-eks-fluentbit](docs/cloudwatch/cloudwatch-eks-fluentbit.md) | Fluent Bit on EKS, DaemonSet |
| [cloudwatch-embedded-metrics](docs/cloudwatch/cloudwatch-embedded-metrics.md) | EMF, Lambda/컨테이너 구조화 지표 |
| [cloudwatch-evidently](docs/cloudwatch/cloudwatch-evidently.md) | Feature Flag, A/B 테스트, Kill Switch |
| [cloudwatch-log-insights](docs/cloudwatch/cloudwatch-log-insights.md) | Logs Insights 쿼리, 집계/파싱 |
| [cloudwatch-metric-math](docs/cloudwatch/cloudwatch-metric-math.md) | Metric Math, ANOMALY_DETECTION |
| [cloudwatch-rum](docs/cloudwatch/cloudwatch-rum.md) | RUM, Core Web Vitals |
| [cloudwatch-synthetics](docs/cloudwatch/cloudwatch-synthetics.md) | Synthetics Canary, Puppeteer |

### Network [`docs/network/`](docs/network/)

| 파일 | 주제 |
|------|------|
| [aws-transit-gateway](docs/network/aws-transit-gateway.md) | Hub-and-Spoke, 멀티 VPC 라우팅 |
| [nlb-ec2-port-forwarding](docs/network/nlb-ec2-port-forwarding.md) | NLB 포트 포워딩, 헬스체크 |
| [route53-failover-routing](docs/network/route53-failover-routing.md) | Failover 라우팅, Active-Passive |
| [vpc-endpoint](docs/network/vpc-endpoint.md) | Gateway/Interface Endpoint, Private DNS |
| [vpc-flow-logs-analysis](docs/network/vpc-flow-logs-analysis.md) | Flow Logs 분석, Athena 쿼리 |
| [vpc-subnet-design](docs/network/vpc-subnet-design.md) | CIDR 설계, 3계층 구조, IP 고갈 |

### Security [`docs/security/`](docs/security/)

| 파일 | 주제 |
|------|------|
| [aws-credentials](docs/security/aws-credentials.md) | 자격 증명 우선순위, SSO 활용 |
| [aws-security-imds](docs/security/aws-security-imds.md) | IMDSv1 vs v2, SSRF 방어 |
| [aws-security-sha256-usage](docs/security/aws-security-sha256-usage.md) | SHA-256, Lambda hash, Sig V4 |
| [cloudtrail-security-audit](docs/security/cloudtrail-security-audit.md) | CloudTrail, 보안 이벤트 알람 |
| [iam-permission-boundary](docs/security/iam-permission-boundary.md) | Permission Boundary, 에스컬레이션 방지 |
| [waf-rate-limiting](docs/security/waf-rate-limiting.md) | WAF Managed Rules, Rate-based Rule |

### Storage [`docs/storage/`](docs/storage/)

| 파일 | 주제 |
|------|------|
| [ecr-lifecycle-policy](docs/storage/ecr-lifecycle-policy.md) | ECR 태그 전략, 취약점 스캔 |
| [efs-access-point](docs/storage/efs-access-point.md) | EFS Access Point, 다중 테넌트 격리 |
| [s3-lifecycle-intelligent-tiering](docs/storage/s3-lifecycle-intelligent-tiering.md) | S3 스토리지 클래스, Intelligent-Tiering |

### Database [`docs/database/`](docs/database/)

| 파일 | 주제 |
|------|------|
| [elasticache-redis-cluster](docs/database/elasticache-redis-cluster.md) | Redis 클러스터 모드, Eviction |
| [rds-aurora-cluster](docs/database/rds-aurora-cluster.md) | Aurora Writer/Reader, 페일오버 |
| [rds-parameter-group](docs/database/rds-parameter-group.md) | 파라미터 튜닝, 슬로우 쿼리 로깅 |

### Direct Connect [`docs/dx/`](docs/dx/)

| 파일 | 주제 |
|------|------|
| [dx-bgp-vif-down-scenario](docs/dx/dx-bgp-vif-down-scenario.md) | BGP/VIF Down, 영향 분석 |
| [dx-building-resiliency](docs/dx/dx-building-resiliency.md) | High/Maximum Resiliency, VPN 백업 |
| [dx-location](docs/dx/dx-location.md) | DX 로케이션, Cross-Connect, LOA-CFA |
| [dx-monitoring](docs/dx/dx-monitoring.md) | CloudWatch DX 지표, 광레벨 |
| [dx-packet-loss](docs/dx/dx-packet-loss.md) | 패킷 손실, microburst, 물리 오류 |

### Cost & Governance [`docs/cost/`](docs/cost/)

| 파일 | 주제 |
|------|------|
| [aws-cost-optimization](docs/cost/aws-cost-optimization.md) | SP/RI/Spot, 미사용 리소스 탐지 |
| [aws-organizations-multi-account](docs/cost/aws-organizations-multi-account.md) | OU 구조, SCP, IAM Identity Center |

### Platform [`docs/platform/`](docs/platform/)

| 파일 | 주제 |
|------|------|
| [aws-cli-internals](docs/platform/aws-cli-internals.md) | botocore 구조, SigV4, 페이지네이션 |
| [aws-codedeploy](docs/platform/aws-codedeploy.md) | In-Place/Blue-Green 배포, AppSpec |

---

## 코드 예제

### CLI 스크립트 [`cli/`](cli/)
EC2, EKS, IAM, VPC, RDS, S3, CloudWatch, 비용 분석 쿼리 모음

### SDK 모듈 [`sdk/`](sdk/)
Python boto3 기반 쿼리 모듈 — 페이지네이션, 에러 처리 포함

### Lambda 함수 [`lambda/`](lambda/)
EC2 스케줄러, Slack 알림, S3 이벤트 처리, EBS 스냅샷 정리, 비용 이상 감지 등

---

## Claude 협업

이 프로젝트는 Claude Code와 협업하도록 최적화되어 있습니다.

| 커맨드 | 설명 |
|--------|------|
| `/new-doc ec2 nitro-system` | 신규 문서 스캐폴딩 |
| `/new-runbook rds failover` | 운영 Runbook 생성 |
| `/review-doc docs/eks/eks-irsa.md` | 문서 품질 검토 |
| `/search-kb IRSA 토큰` | 지식 베이스 검색 |
