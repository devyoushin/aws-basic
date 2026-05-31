# AWS 코드 작성 규칙 (AWS Code Conventions)

이 저장소에서 AWS CLI, Terraform, SDK 예시 코드 작성 시 따라야 할 규칙입니다.

---

## 1. AWS CLI 규칙

### 기본 형식
```bash
aws <service> <command> \
  --option1 value1 \
  --option2 value2 \
  --region ap-northeast-2 \
  --output json
```

- `\` 로 줄 바꿈하여 가독성 확보
- `--region` 항상 명시 (환경 변수 의존 금지)
- `--output json` 명시 (기본값 의존 금지)
- `--query` 사용 시 JMESPath 표현식 주석으로 설명

### 플레이스홀더 표기
```bash
aws ec2 describe-instances \
  --instance-ids <INSTANCE_ID> \       # i-0123456789abcdef0
  --region <REGION>                     # ap-northeast-2
```

## 2. Terraform 규칙

### Provider 버전
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
```

### 리소스 명명 규칙
```hcl
# 형식: {환경}-{서비스}-{역할}
resource "aws_instance" "prod_web_app" { ... }
resource "aws_security_group" "prod_web_sg" { ... }
```

### 태그 필수 항목
```hcl
tags = {
  Name        = "<RESOURCE_NAME>"
  Environment = "<prod|staging|dev>"
  Team        = "<TEAM_NAME>"
  ManagedBy   = "terraform"
}
```

### 민감 정보 처리
```hcl
# 하드코딩 금지 — 반드시 variable 또는 SSM Parameter Store 사용
variable "db_password" {
  type      = string
  sensitive = true
}
```

## 3. Python/boto3 규칙

### 기본 클라이언트 생성
```python
import boto3

# 리전 명시 필수
client = boto3.client("ec2", region_name="ap-northeast-2")
```

### 페이지네이션 처리
```python
# list_* 계열 API는 반드시 paginator 사용
paginator = client.get_paginator("describe_instances")
for page in paginator.paginate():
    for reservation in page["Reservations"]:
        ...
```

### 에러 처리
```python
from botocore.exceptions import ClientError

try:
    response = client.describe_instances(InstanceIds=["i-xxx"])
except ClientError as e:
    error_code = e.response["Error"]["Code"]
    print(f"에러: {error_code}")
```

## 4. IAM 정책 규칙

- **최소 권한 원칙**: `*` 와일드카드 리소스 사용 시 반드시 이유 주석 추가
- **조건 키 활용**: 리소스 태그 기반 조건 권장
- **예시 형식**:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowEC2ReadOnly",
      "Effect": "Allow",
      "Action": [
        "ec2:Describe*"
      ],
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "aws:RequestedRegion": "ap-northeast-2"
        }
      }
    }
  ]
}
```

## 5. 리전 및 계정 정보

- 예시 코드의 기본 리전: `ap-northeast-2` (서울)
- 계정 ID 플레이스홀더: `123456789012`
- ARN 형식: `arn:aws:<service>:ap-northeast-2:123456789012:<resource>`
