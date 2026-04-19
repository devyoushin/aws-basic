# WAF 규칙 구성 & Rate Limiting

## 1. 개요

AWS WAF는 ALB, CloudFront, API Gateway, AppSync 앞단에서 HTTP/HTTPS 트래픽을 필터링하는 웹 방화벽이다.
Managed Rule Group으로 OWASP Top 10, 봇 트래픽을 빠르게 차단하고,
Rate-based Rule로 DDoS/무차별 대입 공격을 IP 단위로 자동 차단할 수 있다.
ACL당 $5/월 + 규칙당 $1/월 + 요청 처리 비용($0.60/백만 요청) 구조다.

---

## 2. 설명

### 2.1 핵심 개념

**WAF 구성 요소**

```
Web ACL
  └── Rule Group (규칙 묶음)
        ├── Managed Rule Group (AWS/마켓플레이스 제공)
        └── Custom Rule
              ├── Rate-based Rule (IP당 요청 수 제한)
              ├── IP Set (허용/차단 IP 목록)
              └── Regex Pattern Set (정규식 패턴 매칭)
```

**AWS Managed Rule Group 주요 목록**

| 그룹 이름 | 용도 | 비용 |
|---------|------|------|
| `AWSManagedRulesCommonRuleSet` | OWASP Top 10 기본 (XSS, SQLi 등) | 무료 |
| `AWSManagedRulesKnownBadInputsRuleSet` | 알려진 악성 입력 패턴 | 무료 |
| `AWSManagedRulesSQLiRuleSet` | SQL Injection 특화 | 무료 |
| `AWSManagedRulesLinuxRuleSet` | Linux 환경 특화 공격 | 무료 |
| `AWSManagedRulesAmazonIpReputationList` | 악성 IP 목록 (AWS 위협 인텔리전스) | 무료 |
| `AWSManagedRulesBotControlRuleSet` | 봇 탐지 및 차단 | $10/월 + 요청 비용 |

**Rule 우선순위 (Priority)**

```
숫자가 낮을수록 먼저 평가
ALLOW/BLOCK 결정 나면 이후 규칙 평가 안 함

권장 순서:
  1 (낮음): 허용 IP 목록 (운영팀 IP) → Allow
  2: Rate Limiting → Block 과도한 요청
  3: Managed Rules → Block 알려진 공격
  4: 커스텀 규칙 → Block/Count
  5 (높음): 기본 Action (Allow)
```

---

### 2.2 실무 적용 코드

**Terraform — WAF Web ACL 전체 구성**

```hcl
resource "aws_wafv2_web_acl" "main" {
  name  = "main-waf"
  scope = "REGIONAL"   # ALB용. CloudFront는 "CLOUDFRONT" (us-east-1)

  default_action {
    allow {}   # 기본: 허용 (Blocklist 방식)
  }

  # 규칙 1: 운영팀 IP 화이트리스트 (최우선)
  rule {
    name     = "AllowOpsTeamIPs"
    priority = 1

    action {
      allow {}
    }

    statement {
      ip_set_reference_statement {
        arn = aws_wafv2_ip_set.ops_team.arn
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "AllowOpsTeamIPs"
      sampled_requests_enabled   = true
    }
  }

  # 규칙 2: Rate Limiting (IP당 5분간 2000 요청)
  rule {
    name     = "RateLimitPerIP"
    priority = 2

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = 2000     # 5분(300초)당 요청 수
        aggregate_key_type = "IP"

        # 특정 URI에만 더 엄격한 제한 (로그인 엔드포인트)
        scope_down_statement {
          byte_match_statement {
            search_string         = "/api/auth/login"
            field_to_match {
              uri_path {}
            }
            text_transformation {
              priority = 0
              type     = "LOWERCASE"
            }
            positional_constraint = "STARTS_WITH"
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "RateLimitPerIP"
      sampled_requests_enabled   = true
    }
  }

  # 규칙 3: AWS Managed - OWASP Top 10
  rule {
    name     = "AWSManagedCommonRules"
    priority = 10

    override_action {
      none {}   # Managed Rule의 기본 액션 사용 (Block)
      # count {}  # 처음엔 Count 모드로 영향도 확인 후 활성화
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"

        # 특정 규칙만 Count로 완화 (False Positive 대응)
        rule_action_override {
          name          = "SizeRestrictions_BODY"
          action_to_use {
            count {}   # 대용량 바디가 정상인 API에서 False Positive 방지
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "AWSManagedCommonRules"
      sampled_requests_enabled   = true
    }
  }

  # 규칙 4: AWS Managed - 알려진 악성 IP
  rule {
    name     = "AWSManagedIPReputation"
    priority = 11

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesAmazonIpReputationList"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "AWSManagedIPReputation"
      sampled_requests_enabled   = true
    }
  }

  # 규칙 5: SQL Injection 특화
  rule {
    name     = "AWSManagedSQLi"
    priority = 12

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesSQLiRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "AWSManagedSQLi"
      sampled_requests_enabled   = true
    }
  }

  # 규칙 6: 커스텀 차단 IP 목록
  rule {
    name     = "BlockBadActors"
    priority = 20

    action {
      block {}
    }

    statement {
      ip_set_reference_statement {
        arn = aws_wafv2_ip_set.bad_actors.arn
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "BlockBadActors"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "main-waf"
    sampled_requests_enabled   = true
  }

  tags = { Environment = "production" }
}

# WAF → ALB 연결
resource "aws_wafv2_web_acl_association" "alb" {
  resource_arn = aws_lb.main.arn
  web_acl_arn  = aws_wafv2_web_acl.main.arn
}
```

**IP Set 관리**

```hcl
# 운영팀 IP 화이트리스트
resource "aws_wafv2_ip_set" "ops_team" {
  name               = "ops-team-ips"
  scope              = "REGIONAL"
  ip_address_version = "IPV4"

  addresses = [
    "203.0.113.10/32",   # 사무실 공인 IP
    "203.0.113.20/32",   # VPN 출구 IP
  ]
}

# 차단 IP 목록 (동적 업데이트 가능)
resource "aws_wafv2_ip_set" "bad_actors" {
  name               = "bad-actors"
  scope              = "REGIONAL"
  ip_address_version = "IPV4"

  addresses = var.blocked_ips   # 변수로 관리
}

# Lambda로 차단 IP 자동 갱신
resource "aws_lambda_function" "update_ip_set" {
  function_name = "waf-ip-set-updater"
  handler       = "index.handler"
  runtime       = "python3.12"

  environment {
    variables = {
      IP_SET_ID    = aws_wafv2_ip_set.bad_actors.id
      IP_SET_NAME  = aws_wafv2_ip_set.bad_actors.name
      IP_SET_SCOPE = "REGIONAL"
    }
  }
}
```

**로그인 엔드포인트 전용 엄격한 Rate Limit**

```hcl
# 로그인 시도 제한 (IP당 1분간 10회)
resource "aws_wafv2_web_acl" "auth_strict" {
  # 별도 ACL을 API Gateway에 연결하거나 규칙 추가
  rule {
    name     = "StrictLoginRateLimit"
    priority = 1

    action {
      block {
        custom_response {
          response_code = 429

          response_header {
            name  = "Retry-After"
            value = "60"
          }
        }
      }
    }

    statement {
      rate_based_statement {
        limit              = 10    # 1분간 10회
        aggregate_key_type = "IP"

        scope_down_statement {
          and_statement {
            statement {
              byte_match_statement {
                search_string = "/auth/login"
                field_to_match { uri_path {} }
                text_transformation { priority = 0; type = "LOWERCASE" }
                positional_constraint = "ENDS_WITH"
              }
            }
            statement {
              byte_match_statement {
                search_string = "POST"
                field_to_match { method {} }
                text_transformation { priority = 0; type = "UPPERCASE" }
                positional_constraint = "EXACTLY"
              }
            }
          }
        }
      }
    }
    # ...
  }
}
```

---

### 2.3 보안/비용 Best Practice

- **처음엔 Count 모드로 시작**: Managed Rule을 바로 Block으로 적용하면 False Positive로 정상 트래픽 차단 위험. 2주간 Count 모드로 로그 분석 후 Block 전환
- **WAF 로그 S3에 저장**: CloudWatch Logs는 비쌈. S3 + Athena 조합이 대용량 로그 분석에 효율적
- **Scope Down으로 비용 절감**: Rate-based Rule에 Scope Down을 추가하면 특정 URI에만 적용. 전체 요청 평가 비용 감소
- **Bot Control은 신중하게**: $10/월 + 추가 요청 비용 발생. 실제 봇 트래픽 문제가 있을 때만 활성화

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**정상 트래픽이 차단됨 (False Positive)**

```bash
# WAF 로그에서 차단 이유 확인
aws logs filter-log-events \
  --log-group-name /aws/wafv2/main-waf \
  --filter-pattern '{ $.action = "BLOCK" }' \
  --start-time $(date -d '1 hour ago' +%s000)

# 특정 규칙이 차단하는지 확인
# terminatingRuleId 필드에 어떤 규칙이 Block했는지 기록됨

# 해결:
# 1. 해당 규칙을 Count 모드로 전환
# 2. 특정 URI/IP를 Rule의 scope_down에서 제외
# 3. IP를 화이트리스트에 추가
```

**Rate Limit이 예상보다 일찍 발동**

```bash
# Rate-based rule은 5분 슬라이딩 윈도우 기준
# limit=2000이면 5분간 2001번째 요청부터 차단

# WAF 샘플 요청에서 Rate Limit 발동 IP 확인
aws wafv2 get-sampled-requests \
  --web-acl-arn arn:aws:wafv2:ap-northeast-2:123456789012:regional/webacl/main-waf/xxx \
  --rule-metric-name RateLimitPerIP \
  --scope REGIONAL \
  --time-window StartTime=$(date -d '1 hour ago' +%s),EndTime=$(date +%s) \
  --max-items 100
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: WAF가 CloudFront에 있는데 ALB에도 추가로 달아야 하나요?**
A: CloudFront를 반드시 거치도록 설계됐다면 CloudFront WAF만으로 충분합니다. 하지만 ALB가 직접 노출될 수 있다면 ALB에도 별도 WAF를 추가하거나 Security Group으로 CloudFront IP만 허용하는 것이 안전합니다.

**Q: Rate Limit을 IP가 아닌 사용자 기준으로 할 수 있나요?**
A: WAF v2의 `aggregate_key_type`을 `FORWARDED_IP`(XFF 헤더) 또는 `CUSTOM_KEYS`로 설정하면 특정 헤더(JWT, API Key) 기반으로 Rate Limit 가능합니다. 단 헤더 위조에 주의하세요.

---

## 4. 모니터링 및 알람

```hcl
# 차단 요청 급증 알람
resource "aws_cloudwatch_metric_alarm" "waf_blocked" {
  alarm_name          = "waf-blocked-requests-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "BlockedRequests"
  namespace           = "AWS/WAFV2"
  period              = 300
  statistic           = "Sum"
  threshold           = 1000   # 5분간 1000건 차단 시 알람

  dimensions = {
    WebACL = aws_wafv2_web_acl.main.name
    Region = var.region
    Rule   = "ALL"
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
}

# WAF 로그 S3 저장
resource "aws_wafv2_web_acl_logging_configuration" "main" {
  log_destination_configs = [aws_kinesis_firehose_delivery_stream.waf_logs.arn]
  resource_arn            = aws_wafv2_web_acl.main.arn

  # 특정 헤더 로깅 제외 (개인정보 보호)
  redacted_fields {
    single_header {
      name = "authorization"
    }
  }
}
```

---

## 5. TIP

- **WAF Captcha**: Rate Limit 대신 Captcha Challenge를 사용하면 사람 사용자는 통과시키고 봇만 차단. 반자동화 공격에 효과적
- **Security Automations**: AWS Security Automations for AWS WAF 솔루션을 사용하면 HTTP flood, Scanner/Probe 등을 자동 감지하고 IP 차단 자동화 가능 (CloudFormation 기반)
- **Shield Advanced 조합**: DDoS 대규모 공격에는 WAF + Shield Advanced 조합. Shield Advanced는 $3,000/월이지만 WAF 비용 면제 + DDoS 비용 환급 포함
- **Athena로 WAF 로그 분석**: WAF 로그를 S3에 저장하고 Athena로 차단 패턴, 상위 공격 IP, 공격 시간대를 분석. `cloudtrail-security-audit.md`의 Athena 테이블 생성 방식 참고
