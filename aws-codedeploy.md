# AWS CodeDeploy

## 1. 개요
- AWS CodeDeploy는 EC2, ECS, Lambda, 온프레미스 서버에 애플리케이션을 **자동화된 방식으로 배포**하는 완전 관리형 서비스입니다.
- 배포 중단 없이 롤링/블루그린 방식으로 교체하며, 실패 시 자동 롤백을 지원합니다.
- AWS CodePipeline과 연동하여 CI/CD 파이프라인의 배포(Deploy) 단계를 담당합니다.

---

## 2. 설명

### 2.1 핵심 개념

#### 배포 대상 플랫폼
| 플랫폼 | 설명 |
|--------|------|
| **EC2/온프레미스** | CodeDeploy Agent가 설치된 인스턴스에 파일+스크립트 배포 |
| **ECS** | Task Definition 업데이트 + ALB 트래픽 전환 (Blue/Green) |
| **Lambda** | 새 함수 버전으로 트래픽 단계적 이동 (Canary, Linear) |

#### 주요 구성요소
```
Application
  └── Deployment Group (배포 대상 그룹 — EC2 Tag, ASG, ECS Service 등)
        └── Deployment (개별 배포 실행)
              └── AppSpec File (배포 지시서)
```

| 구성요소 | 역할 |
|---------|------|
| **Application** | 배포 단위 이름 (서비스 단위로 생성) |
| **Deployment Group** | 배포 대상 인스턴스 집합 (EC2 Tag 또는 ASG로 지정) |
| **Deployment Configuration** | 한 번에 배포할 비율 (AllAtOnce, HalfAtATime, OneAtATime, 커스텀) |
| **AppSpec** | 배포 순서·파일 위치·Lifecycle Hook 스크립트를 정의하는 YAML |
| **Revision** | S3 또는 GitHub에 저장된 배포 아티팩트 (소스+appspec.yml) |

#### 배포 타입 비교
| 항목 | In-Place | Blue/Green |
|------|----------|-----------|
| 대상 | 동일 인스턴스에 교체 | 새 인스턴스 세트 생성 후 트래픽 전환 |
| 다운타임 | DeploymentConfig에 따라 발생 가능 | 거의 없음 |
| 롤백 속도 | 느림 (재배포 필요) | 빠름 (이전 Fleet으로 트래픽 복원) |
| 비용 | 추가 인스턴스 없음 | 잠시 2배 인스턴스 비용 |
| 권장 상황 | 개발/스테이징 환경 | 프로덕션 무중단 배포 |

---

### 2.2 AppSpec 파일 구조 (EC2/온프레미스)

```yaml
# appspec.yml — 아티팩트 루트에 반드시 위치
version: 0.0
os: linux

files:
  - source: /build/app.jar          # 아티팩트 내 경로
    destination: /opt/myapp/        # 인스턴스 내 목적지 경로
  - source: /config/application.yml
    destination: /opt/myapp/config/

permissions:
  - object: /opt/myapp/app.jar
    owner: ec2-user
    group: ec2-user
    mode: "755"

hooks:
  BeforeInstall:
    - location: scripts/stop_server.sh
      timeout: 60
      runas: root
  AfterInstall:
    - location: scripts/install_deps.sh
      timeout: 120
      runas: ec2-user
  ApplicationStart:
    - location: scripts/start_server.sh
      timeout: 60
      runas: root
  ValidateService:
    - location: scripts/health_check.sh
      timeout: 30
      runas: ec2-user
```

#### EC2 In-Place Lifecycle Hook 순서
```
Start
  → BeforeBlockTraffic    (ALB에서 트래픽 차단 전)
  → BlockTraffic          (ALB 등록 해제)
  → AfterBlockTraffic
  → ApplicationStop       (기존 앱 종료)
  → DownloadBundle        (S3/GitHub에서 아티팩트 다운로드)
  → BeforeInstall         (파일 복사 전 정리 작업)
  → Install               (파일 복사)
  → AfterInstall          (설정 파일 적용, 의존성 설치)
  → ApplicationStart      (새 버전 앱 기동)
  → ValidateService       (헬스체크)
  → BeforeAllowTraffic    (ALB 재등록 전)
  → AllowTraffic          (ALB 타겟 등록)
  → AfterAllowTraffic
End
```

---

### 2.3 Terraform 구성 예시 (EC2 In-Place)

```hcl
# CodeDeploy Application
resource "aws_codedeploy_app" "myapp" {
  name             = "myapp"
  compute_platform = "Server"   # Server | ECS | Lambda
}

# IAM Role for CodeDeploy
resource "aws_iam_role" "codedeploy" {
  name = "codedeploy-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "codedeploy.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "codedeploy" {
  role       = aws_iam_role.codedeploy.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSCodeDeployRole"
}

# Deployment Group (ASG 기반 Blue/Green)
resource "aws_codedeploy_deployment_group" "myapp" {
  app_name               = aws_codedeploy_app.myapp.name
  deployment_group_name  = "myapp-prod"
  service_role_arn       = aws_iam_role.codedeploy.arn
  deployment_config_name = "CodeDeployDefault.OneAtATime"

  autoscaling_groups = [aws_autoscaling_group.myapp.name]

  deployment_style {
    deployment_option = "WITH_TRAFFIC_CONTROL"  # ALB와 연동
    deployment_type   = "BLUE_GREEN"
  }

  blue_green_deployment_config {
    deployment_ready_option {
      action_on_timeout    = "CONTINUE_DEPLOYMENT"
      wait_time_in_minutes = 0
    }
    green_fleet_provisioning_option {
      action = "COPY_AUTO_SCALING_GROUP"  # 기존 ASG 설정 복제
    }
    terminate_blue_instances_on_deployment_success {
      action                           = "TERMINATE"
      termination_wait_time_in_minutes = 5  # 트래픽 전환 후 5분 대기 후 종료
    }
  }

  load_balancer_info {
    target_group_info {
      name = aws_lb_target_group.myapp.name
    }
  }

  auto_rollback_configuration {
    enabled = true
    events  = ["DEPLOYMENT_FAILURE", "DEPLOYMENT_STOP_ON_ALARM"]
  }

  alarm_configuration {
    alarms  = ["myapp-5xx-alarm", "myapp-unhealthy-hosts-alarm"]
    enabled = true
  }
}

# Custom Deployment Configuration (배포 속도 제어)
resource "aws_codedeploy_deployment_config" "canary_25" {
  deployment_config_name = "myapp-canary-25percent"
  compute_platform       = "Server"

  minimum_healthy_hosts {
    type  = "FLEET_PERCENT"
    value = 75  # 최소 75%는 항상 Healthy 유지
  }
}
```

#### CodeDeploy Agent 설치 (UserData)
```bash
#!/bin/bash
# AL2023 기준
dnf install -y ruby wget
cd /tmp
wget https://aws-codedeploy-ap-northeast-2.s3.ap-northeast-2.amazonaws.com/latest/install
chmod +x ./install
./install auto
systemctl enable codedeploy-agent
systemctl start codedeploy-agent
```

#### S3에 Revision 업로드 및 배포 트리거 (AWS CLI)
```bash
# 아티팩트 패키징 및 S3 업로드
aws deploy push \
  --application-name myapp \
  --s3-location s3://my-artifacts-bucket/myapp/release-$(date +%Y%m%d%H%M%S).zip \
  --source .

# 배포 실행
aws deploy create-deployment \
  --application-name myapp \
  --deployment-group-name myapp-prod \
  --s3-location bucket=my-artifacts-bucket,key=myapp/release-latest.zip,bundleType=zip \
  --description "v1.2.3 release"

# 배포 상태 확인
aws deploy get-deployment --deployment-id d-XXXXXXXXX
```

---

### 2.4 보안/비용 Best Practice

| 항목 | 권장 설정 |
|------|----------|
| **IAM 최소 권한** | EC2 인스턴스 프로파일에 S3 버킷 접근 권한만 부여 (GetObject) |
| **S3 버킷 암호화** | 아티팩트 버킷에 SSE-KMS 적용 |
| **Blue/Green 비용** | `termination_wait_time_in_minutes`를 짧게 설정해 이중 비용 최소화 |
| **배포 알람 연동** | 5xx 에러율, Unhealthy Host 알람과 연동해 자동 롤백 |
| **Agent 버전 고정** | Golden AMI에 특정 버전 Agent를 포함시켜 일관성 확보 |

---

## 3. 트러블슈팅

### 3.1 주요 이슈

#### [이슈 1] Deployment stuck at "Pending" 상태
- **증상:** 배포를 시작했는데 인스턴스가 "Pending" 상태에서 멈춤
- **원인:**
  1. CodeDeploy Agent가 실행 중이지 않음
  2. 인스턴스가 S3 엔드포인트에 접근 불가 (VPC 내부 Private 인스턴스)
  3. EC2 인스턴스 프로파일에 `AmazonS3ReadOnlyAccess` 또는 해당 버킷 권한 없음
- **해결:**
```bash
# Agent 상태 확인
sudo systemctl status codedeploy-agent

# Agent 로그 확인
sudo tail -f /var/log/aws/codedeploy-agent/codedeploy-agent.log

# S3 접근 테스트 (인스턴스 내에서)
aws s3 ls s3://my-artifacts-bucket/ --region ap-northeast-2

# Private 서브넷이면 VPC Endpoint (S3 Gateway) 또는 NAT Gateway 확인
```

#### [이슈 2] ValidateService Hook 실패로 롤백
- **증상:** 앱은 기동됐지만 헬스체크 스크립트가 실패해 자동 롤백
- **원인:** Hook `timeout` 값이 짧거나, 앱 워밍업 시간이 부족
- **해결:**
```bash
# health_check.sh 예시 — 재시도 로직 포함
#!/bin/bash
MAX_RETRIES=12
WAIT_SEC=5
for i in $(seq 1 $MAX_RETRIES); do
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/health)
  if [ "$HTTP_CODE" = "200" ]; then
    echo "Health check passed (attempt $i)"
    exit 0
  fi
  echo "Attempt $i failed (HTTP $HTTP_CODE), waiting ${WAIT_SEC}s..."
  sleep $WAIT_SEC
done
echo "Health check failed after $MAX_RETRIES attempts"
exit 1
```
```yaml
# appspec.yml — timeout 늘리기
hooks:
  ValidateService:
    - location: scripts/health_check.sh
      timeout: 120   # 기본 30초 → 120초로 상향
```

#### [이슈 3] Blue/Green 배포 후 이전 인스턴스가 종료 안 됨
- **증상:** Blue 인스턴스가 계속 Running 상태로 남아 비용 발생
- **원인:** `termination_wait_time_in_minutes`를 높게 설정하거나, 수동 배포(Manual) 옵션 선택
- **해결:** `CONTINUE_DEPLOYMENT` + `TERMINATE` 조합으로 자동 종료 설정

#### [이슈 4] AllowTraffic 단계에서 멈춤
- **증상:** ALB 타겟 등록 후 배포가 진행되지 않음
- **원인:** ALB 헬스체크 실패로 타겟이 `healthy` 상태가 되지 않음
- **해결:** 보안 그룹에서 ALB → EC2 헬스체크 포트(예: 8080) 인바운드 허용 여부 확인

---

### 3.2 자주 발생하는 문제 (Q&A)

**Q: appspec.yml이 없다는 오류가 발생합니다**
- A: appspec.yml은 아티팩트(zip) **최상위 루트**에 있어야 합니다. 서브 디렉토리에 있으면 인식하지 못합니다.
```bash
# 올바른 zip 구조
myapp.zip
├── appspec.yml        # 루트에 위치
├── build/
│   └── app.jar
└── scripts/
    ├── start_server.sh
    └── health_check.sh
```

**Q: 배포는 성공했는데 실제 파일이 바뀌지 않았습니다**
- A: `BeforeInstall` Hook에서 이전 버전 파일을 삭제하는 정리 로직이 없으면 기존 파일이 남을 수 있습니다.
```bash
# scripts/stop_server.sh
#!/bin/bash
systemctl stop myapp || true
rm -rf /opt/myapp/build/   # 이전 아티팩트 정리
```

**Q: CodeDeploy Agent가 설치됐는데 인스턴스가 Deployment Group에 보이지 않습니다**
- A: EC2 태그가 Deployment Group에 설정한 태그와 정확히 일치하는지, Agent가 실행 중인지 확인합니다.
```bash
# Agent 버전 및 연결 상태 확인
sudo /opt/codedeploy-agent/bin/codedeploy-agent status
```

---

## 4. 모니터링 및 알람

### CloudWatch 지표

```hcl
# 배포 실패 알람
resource "aws_cloudwatch_metric_alarm" "deploy_failure" {
  alarm_name          = "codedeploy-deployment-failure"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "DeploymentsFailed"
  namespace           = "AWS/CodeDeploy"
  period              = 300
  statistic           = "Sum"
  threshold           = 1

  dimensions = {
    Application     = "myapp"
    DeploymentGroup = "myapp-prod"
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  alarm_description = "CodeDeploy 배포 실패 감지"
}
```

### 배포 이벤트 SNS 알림 설정
```hcl
resource "aws_codedeploy_deployment_group" "myapp" {
  # ...기존 설정...

  trigger_configuration {
    trigger_events = [
      "DeploymentStart",
      "DeploymentSuccess",
      "DeploymentFailure",
      "DeploymentRollback",
    ]
    trigger_name       = "myapp-deploy-events"
    trigger_target_arn = aws_sns_topic.deploy_alerts.arn
  }
}
```

### 주요 CloudWatch 지표
| 지표 | 네임스페이스 | 설명 |
|------|------------|------|
| `DeploymentsFailed` | AWS/CodeDeploy | 실패한 배포 수 |
| `DeploymentsSucceeded` | AWS/CodeDeploy | 성공한 배포 수 |
| `DeploymentDuration` | AWS/CodeDeploy | 배포 소요 시간 (초) |

---

## 5. TIP

- **배포 속도 vs 안전성 트레이드오프:**
  - `AllAtOnce`: 가장 빠르지만 실패 시 전체 영향. 개발 환경에만 사용.
  - `HalfAtATime`: 50%씩 교체. 배포 중 처리 용량이 절반으로 줄어드는 점 주의.
  - `OneAtATime`: 가장 안전하지만 인스턴스 수에 비례해 오래 걸림.
  - **커스텀 설정 권장:** `FLEET_PERCENT 80` (최소 80% healthy 유지) + `termination_wait_time 5분`

- **CodeDeploy + CodePipeline 연동 시**, S3 아티팩트 버킷은 **같은 리전**에 있어야 합니다.

- **배포 로그 위치:**
  ```
  /opt/codedeploy-agent/deployment-root/{deployment-id}/logs/scripts.log
  /var/log/aws/codedeploy-agent/codedeploy-agent.log
  ```

- **ECS Blue/Green 배포**는 CodeDeploy가 직접 ALB 리스너 룰을 수정해 트래픽을 전환합니다. `aws ecs update-service`와 혼용 금지 (충돌 발생).

- **관련 문서:**
  - [AWS CodeDeploy 공식 문서](https://docs.aws.amazon.com/codedeploy/latest/userguide/welcome.html)
  - [AppSpec 파일 레퍼런스 (EC2)](https://docs.aws.amazon.com/codedeploy/latest/userguide/reference-appspec-file.html)
  - [배포 구성 레퍼런스](https://docs.aws.amazon.com/codedeploy/latest/userguide/deployment-configurations.html)
