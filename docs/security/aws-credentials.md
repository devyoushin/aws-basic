## 1. 개요

AWS CLI, SDK 및 Terraform과 같은 IaC 도구를 사용할 때 가장 기본이 되는 것은 **인증(Authentication)**입니다. 본 문서에서는 로컬 환경(`~/.aws/credentials`)의 작동 원리부터, 실무에서 권장되는 IAM Role 기반의 임시 자격 증명 사용법, 그리고 보안 사고를 방지하기 위한 전략을 다룹니다.

## 2. 설명

### 2.1 자격 증명 우선순위 (Precedence)

AWS 도구는 다음과 같은 순서로 자격 증명을 찾습니다.

1. 명령줄 옵션 (`--profile`)
2. 환경 변수 (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`)
3. 로컬 파일 (`~/.aws/credentials`, `~/.aws/config`)
4. 인스턴스 프로파일 (EC2 IAM Role)

### 2.2 실무 적용 코드 (Terraform & Local Config)

#### [Local] ~/.aws/config & credentials 설정
단순 Access Key 사용보다는 **MFA(Multi-Factor Authentication)**를 강제하는 Profile 설정이 실무 표준입니다.

```bash
# ~/.aws/config
[profile project-prod]
region = ap-northeast-2
output = json
mfa_serial = arn:aws:iam::123456789012:mfa/engineer1

# ~/.aws/credentials
[default]
aws_access_key_id = AKIA...
aws_secret_access_key = wJalr...
```

#### [Terraform] 보안을 고려한 Provider 설정
Access Key를 코드에 하드코딩하는 것은 금기 사항입니다. 반드시 변수나 환경 변수를 활용하세요.

```tf
# provider.tf
provider "aws" {
  region  = "ap-northeast-2"
  profile = var.aws_profile # 실행 시 'project-prod' 전달
}

# IAM User 대신 Role을 사용하는 방식 (추천)
provider "aws" {
  alias = "assumed_role"
  assume_role {
    role_arn     = "arn:aws:iam::123456789012:role/TerraformExecutionRole"
    session_name = "TerraformSession"
  }
}
```

### 2.3 보안(Security) 및 비용(Cost) Best Practice

- **보안**: `IAM User`의 정적 키(Static Key) 사용을 최소화하고, `AWS SSO (IAM Identity Center)`를 통해 단기 자격 증명을 사용하세요.
- **보안**: `.gitignore`에 `.aws/` 및 `.env`를 반드시 추가하여 키 유출을 방지합니다.
- **비용**: 인증 자체는 무료이나, 키 유출로 인한 리소스 오남용은 막대한 비용을 초래합니다. `AWS Budgets`와 연동된 알람 설정이 필수적입니다.

---

## 3. 트러블슈팅 및 모니터링 전략

### 3.1 주요 장애 상황: 자격 증명 만료 또는 권한 부족
가장 빈번한 오류는 `EntityAlreadyExists` 또는 `AccessDenied`입니다.

**모니터링 전략 (CloudWatch / EventBridge):** Access Key가 생성되거나 유출이 의심되는 비정상적 API 호출을 감지해야 합니다.
```yaml
# CloudWatch Alarm (Metric Filter) Example
# 자격 증명 호출 실패가 5분 내 10회 이상 발생 시 알람
Resources:
  AuthFailureAlarm:
    Type: AWS::CloudWatch::Alarm
    Properties:
      AlarmDescription: "Unauthorized API calls detected"
      MetricName: AuthorizationFailureCount
      Namespace: CloudTrailMetrics
      Statistic: Sum
      Period: 300
      EvaluationPeriods: 1
      Threshold: 10
      ComparisonOperator: GreaterThanOrEqualToThreshold
      AlarmActions:
        - !Ref SNSAlertTopic
```

### 3.2 자주 발생하는 문제 (Q&A)

- **Q: `~/.aws/credentials`가 있는데 왜 적용이 안 되나요?**
    - **A**: 환경 변수(`AWS_ACCESS_KEY_ID`)가 설정되어 있는지 확인하세요. 환경 변수가 로컬 파일보다 우선순위가 높습니다. (`unset AWS_ACCESS_KEY_ID`로 해결)
        
- **Q: `ExpiredToken` 에러가 발생합니다.**
    - **A**: STS(Assume Role)를 통해 발급받은 임시 키의 세션 시간이 만료된 것입니다. 다시 로그인(aws sso login 등)이 필요합니다.
        

---

## 4. 참고자료

- [AWS CLI Configuration Variables](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-envvars.html)
- [Terraform AWS Provider Authentication](https://www.google.com/search?q=https://registry.terraform.io/providers/hashicorp/aws/latest/docs%23authentication-and-configuration)
- [AWS IAM Best Practices](https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html)
---

## TIP

- **direnv 활용**: 프로젝트 디렉토리마다 다른 `AWS_PROFILE`을 자동으로 로드하게 설정하면 실수로 운영 환경(Prod)에 배포하는 사고를 줄일 수 있습니다.
- **Leaking Prevention**: `git-secrets`나 `trufflehog` 같은 도구를 CI/CD 파이프라인에 심어두어, 커밋 내에 Access Key가 포함되지 않도록 원천 차단하세요.
- **AWS Vault**: 로컬에서 키를 평문으로 저장하지 않고 OS의 키체인(KeyChain)에 암호화하여 저장하는 `aws-vault` 도구 사용을 강력히 권장합니다.
