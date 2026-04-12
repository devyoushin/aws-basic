# AWS CLI 실무 쿼리 모음

## 파일 목록

| 파일 | 대상 서비스 | 주요 기능 |
|------|------------|-----------|
| `ec2-queries.sh` | EC2, ASG | 인스턴스 조회/필터, EIP/EBS 정리 대상, SG 보안 감사 |
| `eks-queries.sh` | EKS | 클러스터/노드 그룹 현황, 애드온 버전, OIDC |
| `cloudwatch-queries.sh` | CloudWatch | 알람 상태/이력, 메트릭 추출, SQS/Lambda, Logs Insights |
| `iam-queries.sh` | IAM, STS | 사용자/역할/정책 감사, 액세스 키 점검, 권한 시뮬레이션 |
| `cost-queries.sh` | Cost Explorer | 서비스/리전/태그별 비용, 전월비교, RI/SP 활용률, 예측 |
| `s3-rds-queries.sh` | S3, RDS | 버킷 보안 감사, 오브젝트 조회, RDS 상태/파라미터 |
| `rds-queries.sh` | RDS, Aurora | 인스턴스/클러스터, 스냅샷, 이벤트, CloudWatch 지표, 페일오버 |
| `vpc-queries.sh` | VPC | VPC/서브넷/라우팅/SG/피어링, NAT, Flow Logs |

## 빠른 시작

```bash
# 실행 권한 부여
chmod +x cli/*.sh

# 리전 설정 (기본: ap-northeast-2)
export AWS_DEFAULT_REGION=ap-northeast-2

# 예시 실행
./cli/ec2-queries.sh running          # 실행 중인 인스턴스
./cli/ec2-queries.sh open-sg          # 0.0.0.0/0 허용 보안 그룹
./cli/iam-queries.sh old-keys         # 90일 이상 된 액세스 키
./cli/cost-queries.sh this-month      # 이번 달 서비스별 비용
./cli/cloudwatch-queries.sh alarms    # ALARM 상태 알람
./cli/rds-queries.sh clusters         # Aurora 클러스터 목록
./cli/vpc-queries.sh unused-sg        # 미사용 보안 그룹
```

## 자주 쓰는 조합

### 보안 감사 루틴
```bash
./cli/iam-queries.sh no-mfa           # MFA 미설정 사용자
./cli/iam-queries.sh old-keys         # 오래된 액세스 키
./cli/ec2-queries.sh open-sg          # 퍼블릭 인바운드 허용 SG
./cli/s3-rds-queries.sh find-public   # 퍼블릭 S3 버킷
./cli/vpc-queries.sh flow-logs        # Flow Logs 미설정 VPC 확인
```

### 비용 절감 체크리스트
```bash
./cli/cost-queries.sh unused-eip    # 미연결 EIP
./cli/cost-queries.sh unused-ebs    # 미연결 EBS
./cli/cost-queries.sh stopped       # 중지된 EC2
./cli/ec2-queries.sh gp2            # gp3 전환 대상 볼륨
./cli/cost-queries.sh sp-util       # Savings Plans 활용률
./cli/cost-queries.sh ri-util       # RI 활용률 서비스별
```

### 월간 비용 리뷰
```bash
./cli/cost-queries.sh mom           # 이번 달 vs 지난달 비교
./cli/cost-queries.sh by-region     # 리전별 비용 분포
./cli/cost-queries.sh forecast      # 월말 예측 비용
```

## 필요한 IAM 권한

각 스크립트 실행에 필요한 최소 권한입니다.

| 스크립트 | 필요 권한 |
|---------|----------|
| `ec2-queries.sh` | `ec2:Describe*`, `autoscaling:Describe*` |
| `eks-queries.sh` | `eks:Describe*`, `eks:List*` |
| `cloudwatch-queries.sh` | `cloudwatch:Describe*`, `cloudwatch:GetMetric*`, `logs:*Query*`, `logs:Describe*` |
| `iam-queries.sh` | `iam:List*`, `iam:Get*`, `iam:SimulatePrincipalPolicy`, `sts:GetCallerIdentity` |
| `cost-queries.sh` | `ce:GetCostAndUsage`, `ce:GetCostForecast`, `ce:GetSavingsPlans*`, `ce:GetReservation*`, `savingsplans:Describe*`, `ec2:Describe*` |
| `s3-rds-queries.sh` | `s3:ListAllMyBuckets`, `s3:GetBucket*`, `rds:Describe*` |
| `rds-queries.sh` | `rds:Describe*`, `cloudwatch:GetMetricStatistics`, `rds:FailoverDBCluster` |
| `vpc-queries.sh` | `ec2:Describe*` |
