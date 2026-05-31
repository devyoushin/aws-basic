# AWS SDK (boto3) 실무 쿼리 모음

## 파일 목록

| 파일 | 대상 서비스 | 주요 기능 |
|------|------------|-----------|
| `ec2_queries.py` | EC2, ASG | 인스턴스 조회/필터, 보안 그룹 감사, 미사용 리소스 탐지 |
| `eks_queries.py` | EKS | 클러스터/노드 그룹, 애드온 업데이트, IRSA Trust Policy 생성 |
| `cloudwatch_queries.py` | CloudWatch, Logs | 알람/메트릭 조회, SQS/ALB, Logs Insights, 커스텀 메트릭 발행 |
| `s3_queries.py` | S3 | 버킷 보안 감사, S3 Select, Presigned URL, Lifecycle |
| `iam_queries.py` | IAM, STS | 자격 증명 감사, IRSA 역할, 권한 시뮬레이션, Assume Role |
| `cost_explorer.py` | Cost Explorer | 서비스/리전/태그별 비용, 전월 비교, RI/SP 활용률, 예측 |
| `rds_queries.py` | RDS, Aurora | 인스턴스/클러스터, 스냅샷, 이벤트, CloudWatch 지표, 페일오버 |
| `vpc_queries.py` | VPC | VPC/서브넷/라우팅/SG/피어링, NAT, IGW, Flow Logs |

## 빠른 시작

```bash
# 의존성 설치
pip install boto3

# 예시 실행
python sdk/ec2_queries.py running          # 실행 중인 인스턴스
python sdk/iam_queries.py key-info         # 액세스 키 현황
python sdk/cost_explorer.py by-service     # 서비스별 비용
python sdk/cloudwatch_queries.py alarms    # ALARM 상태 알람
python sdk/s3_queries.py audit             # 전체 버킷 보안 감사
python sdk/rds_queries.py clusters         # Aurora 클러스터 목록
python sdk/vpc_queries.py unused-sg        # 미사용 보안 그룹
```

## 필요한 IAM 권한

각 스크립트 실행에 필요한 최소 권한입니다.

| 스크립트 | 필요 권한 |
|---------|----------|
| `ec2_queries.py` | `ec2:Describe*`, `autoscaling:Describe*` |
| `eks_queries.py` | `eks:Describe*`, `eks:List*` |
| `cloudwatch_queries.py` | `cloudwatch:Describe*`, `cloudwatch:GetMetric*`, `cloudwatch:PutMetricData`, `logs:*` |
| `s3_queries.py` | `s3:ListAllMyBuckets`, `s3:GetBucket*`, `s3:PutObject`, `s3:GetObject`, `s3:PutBucketLifecycleConfiguration` |
| `iam_queries.py` | `iam:List*`, `iam:Get*`, `iam:GenerateCredentialReport`, `iam:SimulatePrincipalPolicy`, `sts:*` |
| `cost_explorer.py` | `ce:GetCostAndUsage`, `ce:GetCostForecast`, `ce:GetSavingsPlans*`, `ce:GetReservation*` |
| `rds_queries.py` | `rds:Describe*`, `cloudwatch:GetMetricStatistics`, `rds:FailoverDBCluster` |
| `vpc_queries.py` | `ec2:Describe*` |

## 다른 스크립트에서 import해서 사용하기

```python
# IAM Assume Role로 Cross-account 세션 획득
from iam_queries import get_assumed_session

cross_account_session = get_assumed_session("arn:aws:iam::111122223333:role/DeployRole")
s3 = cross_account_session.client("s3")

# S3 JSON 파일 직접 읽기
from s3_queries import read_json_object

config = read_json_object("my-bucket", "config/prod.json")

# 커스텀 메트릭 발행
from cloudwatch_queries import put_custom_metric

put_custom_metric(
    namespace="MyApp/API",
    metric_name="ActiveConnections",
    value=42,
    unit="Count",
    dimensions=[{"Name": "Environment", "Value": "prod"}],
)

# IRSA Trust Policy 생성
from eks_queries import generate_irsa_trust_policy

policy = generate_irsa_trust_policy(
    cluster_name="prod-cluster",
    namespace="default",
    service_account="my-app-sa",
)
```

## 리전 변경

각 파일 상단의 `session` 변수를 수정하거나, 환경 변수로 지정:

```bash
AWS_DEFAULT_REGION=us-east-1 python sdk/ec2_queries.py running
```
