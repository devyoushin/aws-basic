# CloudWatch Logs Insights

## 1. 개요
- CloudWatch Logs Insights는 로그 그룹에 저장된 로그를 SQL과 유사한 전용 쿼리 언어로 분석하는 서비스
- 별도 인프라 없이 수십억 건의 로그를 수초~수분 내 집계/필터/시각화 가능
- 운영 중 장애 원인 분석, 성능 병목 탐지, 보안 이벤트 조사에 핵심 도구

## 2. 설명
### 2.1 핵심 개념

**쿼리 실행 흐름**
```
로그 그룹 선택 → 시간 범위 설정 → 쿼리 작성 → 결과 분석 → 대시보드 위젯 저장
```

**기본 명령어 구조**
| 명령어 | 설명 |
|--------|------|
| `fields` | 출력할 필드 선택 |
| `filter` | 조건으로 로그 필터링 |
| `stats` | 집계 함수 (count, avg, sum, min, max, percentile) |
| `sort` | 정렬 |
| `limit` | 결과 수 제한 |
| `parse` | 비정형 텍스트에서 필드 추출 (glob/regex) |
| `dedup` | 중복 제거 |
| `display` | 출력 필드 재정의 |

**지원 집계 함수**
- `count(*)`, `count_distinct(field)`
- `avg(field)`, `sum(field)`, `min(field)`, `max(field)`
- `percentile(field, 50)` — p50, p90, p99 등
- `stddev(field)` — 표준편차

### 2.2 실무 적용 코드

**기본 필터 — 에러 로그 조회**
```sql
fields @timestamp, @message
| filter @message like /ERROR/
| sort @timestamp desc
| limit 100
```

**특정 시간대 에러 집계**
```sql
fields @timestamp, @message
| filter @message like /Exception/
| stats count(*) as errorCount by bin(5m)
| sort @timestamp asc
```

**Lambda 콜드 스타트 분석**
```sql
filter @type = "REPORT"
| stats
    count() as invocations,
    count(@initDuration) as coldStarts,
    avg(@initDuration) as avgInitDuration,
    max(@initDuration) as maxInitDuration,
    avg(@duration) as avgDuration,
    max(@maxMemoryUsed) as maxMemUsed
by bin(1h)
```

**Lambda 에러율 및 실행 시간 분포**
```sql
filter @type = "REPORT"
| stats
    percentile(@duration, 50) as p50,
    percentile(@duration, 90) as p90,
    percentile(@duration, 99) as p99,
    max(@duration) as max
by bin(5m)
```

**ALB 액세스 로그 — 느린 요청 탐지**
```sql
fields @timestamp, request_url, target_processing_time, elb_status_code
| filter target_processing_time > 1.0
| sort target_processing_time desc
| limit 50
```

**ALB 5XX 에러 URL 별 집계**
```sql
fields elb_status_code, request_url
| filter elb_status_code like /^5/
| stats count(*) as errorCount by request_url
| sort errorCount desc
| limit 20
```

**EKS/컨테이너 — 특정 Pod 로그 필터**
```sql
fields @timestamp, log, kubernetes.pod_name, kubernetes.namespace_name
| filter kubernetes.namespace_name = "prod"
| filter log like /OOMKilled/ or log like /CrashLoopBackOff/
| sort @timestamp desc
```

**parse 명령어 — 비정형 로그 파싱**
```sql
# Nginx 액세스 로그 예시: 127.0.0.1 - - [01/Jan/2024] "GET /api/v1 HTTP/1.1" 200 1234
parse @message '* - - [*] "* * *" * *' as ip, time, method, path, protocol, status, bytes
| filter status >= 400
| stats count(*) as cnt by status, path
| sort cnt desc
```

**정규식으로 필드 추출**
```sql
parse @message /(?P<level>INFO|WARN|ERROR|DEBUG)\s+(?P<msg>.+)/
| filter level = "ERROR"
| stats count(*) as cnt by bin(10m)
```

**VPC Flow Logs — 거부된 트래픽 분석**
```sql
fields @timestamp, srcAddr, dstAddr, dstPort, action
| filter action = "REJECT"
| stats count(*) as rejectCount by srcAddr, dstAddr, dstPort
| sort rejectCount desc
| limit 30
```

**CloudTrail — 루트 계정 로그인 탐지**
```sql
fields @timestamp, userIdentity.type, sourceIPAddress, eventName
| filter userIdentity.type = "Root"
| filter eventName = "ConsoleLogin"
| sort @timestamp desc
```

**AWS CLI로 쿼리 실행**
```bash
# 쿼리 시작
QUERY_ID=$(aws logs start-query \
  --log-group-name "/aws/eks/prod-cluster/application" \
  --start-time $(date -d '1 hour ago' +%s) \
  --end-time $(date +%s) \
  --query-string 'fields @timestamp, @message | filter @message like /ERROR/ | limit 20' \
  --query 'queryId' \
  --output text)

echo "Query ID: $QUERY_ID"

# 결과 조회 (상태가 Complete 될 때까지 대기)
aws logs get-query-results --query-id "$QUERY_ID"
```

**저장된 쿼리(Saved Query) 생성**
```bash
aws logs put-query-definition \
  --name "prod-error-analysis" \
  --log-group-names "/aws/eks/prod-cluster/application" \
  --query-string 'fields @timestamp, @message | filter @message like /ERROR/ | stats count(*) by bin(5m)'
```

**Terraform — 쿼리 결과를 대시보드 위젯으로**
```hcl
resource "aws_cloudwatch_dashboard" "log_analysis" {
  dashboard_name = "log-analysis"

  dashboard_body = jsonencode({
    widgets = [
      {
        type = "log"
        properties = {
          query   = "SOURCE '/aws/eks/prod-cluster/application' | fields @timestamp, @message | filter @message like /ERROR/ | stats count(*) by bin(5m)"
          region  = "ap-northeast-2"
          title   = "에러 발생 추이 (5분 단위)"
          view    = "timeSeries"
        }
      }
    ]
  })
}
```

### 2.3 보안/비용 Best Practice
- **비용**: 스캔된 데이터 1GB당 약 $0.005 — 불필요하게 넓은 시간 범위 쿼리 자제
- 자주 쓰는 쿼리는 Saved Query로 저장해 팀 공유
- 로그 그룹에 보존 기간(Retention) 설정으로 스캔 대상 데이터량 감소
- 여러 로그 그룹을 동시에 쿼리할 때 `SOURCE` 키워드 사용

## 3. 트러블슈팅
### 3.1 주요 이슈

**쿼리 타임아웃 발생**
- 증상: 쿼리 상태가 `Timeout`
- 원인: 시간 범위가 너무 넓거나 로그 볼륨이 과도하게 큼
- 해결:
  - 시간 범위를 1시간 이하로 줄이기
  - `filter`를 먼저 적용해 스캔 데이터 축소
  - 특정 로그 그룹만 타겟팅

**parse 결과가 null로 나오는 경우**
- 증상: parse 이후 필드 값이 `-`
- 원인: 로그 포맷이 패턴과 일치하지 않는 일부 로그 존재
- 해결: `filter ispresent(필드명)`으로 파싱 성공한 로그만 필터

**stats 결과가 예상과 다른 경우**
- 증상: count가 실제보다 적음
- 원인: 로그 수집 지연(최대 5분) 또는 로그 그룹 선택 누락
- 해결: 시간 범위를 실제 이벤트보다 10분 여유있게 설정

### 3.2 자주 발생하는 문제 (Q&A)

- Q: 여러 로그 그룹을 동시에 쿼리하려면?
- A: `SOURCE 'log-group-1', 'log-group-2'` 또는 콘솔에서 최대 50개 로그 그룹 선택 가능

- Q: 쿼리 결과를 S3로 내보낼 수 있나요?
- A: 직접 지원 안 됨. AWS CLI로 결과를 받아 스크립트로 S3 업로드하거나 CloudWatch Logs Export 기능 사용

- Q: Logs Insights와 Athena 중 어떤 걸 써야 하나요?
- A: 실시간/빠른 분석은 Logs Insights, 장기간 대용량 분석이나 S3에 쌓인 로그는 Athena

## 4. 모니터링 및 알람
```bash
# 쿼리 결과로 알람 생성 (Logs Insights → Metric Filter 조합)
# 1. 메트릭 필터 생성
aws logs put-metric-filter \
  --log-group-name "/aws/eks/prod-cluster/application" \
  --filter-name "ErrorCount" \
  --filter-pattern "ERROR" \
  --metric-transformations \
    metricName=ErrorCount,metricNamespace=Custom/AppMetrics,metricValue=1,defaultValue=0

# 2. 해당 메트릭으로 알람 생성
aws cloudwatch put-metric-alarm \
  --alarm-name "app-error-rate-high" \
  --metric-name ErrorCount \
  --namespace Custom/AppMetrics \
  --statistic Sum \
  --period 300 \
  --evaluation-periods 2 \
  --threshold 10 \
  --comparison-operator GreaterThanThreshold \
  --alarm-actions "arn:aws:sns:ap-northeast-2:123456789012:oncall"
```

## 5. TIP
- **`@logStream`, `@log`** 내장 필드로 어느 스트림에서 온 로그인지 확인 가능
- `bin()` 함수의 시간 단위: `1s`, `1m`, `5m`, `1h`, `1d`
- 쿼리 결과를 CloudWatch 대시보드에 Log 위젯으로 바로 고정 가능 (콘솔 "Add to dashboard" 버튼)
- 자주 쓰는 패턴은 팀 Runbook에 Saved Query ID로 링크해두면 장애 시 바로 실행 가능
- 관련 문서: [Logs Insights 쿼리 구문 레퍼런스](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CWL_QuerySyntax.html)
