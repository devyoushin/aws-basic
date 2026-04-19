# {서비스명} — {주제명}

> **파일명 규칙**: `{서비스}-{주제}.md` | **카테고리**: `docs/{카테고리}/`

---

## 1. 개요

{이 기술/기능이 무엇인지 1~3문장 설명}
{왜 알아야 하는지 — 운영 상 의미, 장애 사례 연관성}

**핵심 요약**
- **사용 목적**: {언제 사용하는가}
- **주요 이점**: {왜 쓰는가}
- **관련 서비스**: {함께 자주 사용되는 AWS 서비스}

---

## 2. 설명

### 2.1 핵심 개념

{동작 원리, 주요 차이점, 아키텍처 다이어그램 (ASCII)}

```
[컴포넌트 A] --> [컴포넌트 B] --> [컴포넌트 C]
```

| 항목 | 설명 |
|------|------|
| {개념 1} | {설명} |
| {개념 2} | {설명} |

### 2.2 실무 적용 코드

#### AWS CLI

```bash
aws {service} {command} \
  --option1 <VALUE> \
  --region ap-northeast-2 \
  --output json
```

#### Terraform

```hcl
resource "aws_{resource}" "{name}" {
  # 필수 속성

  tags = {
    Name        = "<RESOURCE_NAME>"
    Environment = "<prod|staging|dev>"
    Team        = "<TEAM_NAME>"
    ManagedBy   = "terraform"
  }
}
```

#### Python (boto3)

```python
import boto3

client = boto3.client("{service}", region_name="ap-northeast-2")

response = client.{method}(
    {param1}="<VALUE>",
)
```

### 2.3 보안/비용 Best Practice

**보안**
- {보안 설정 1}
- {보안 설정 2}

**비용 절감**
- {비용 팁 1}
- {비용 팁 2}

---

## 3. 트러블슈팅

### 3.1 주요 이슈

#### {이슈명}

**증상**
- {증상 설명}
- 오류 메시지: `{에러 텍스트}`

**원인**
- {근본 원인}

**해결 방법**
```bash
# 진단
{진단 명령어}

# 해결
{해결 명령어}
```

> **예방책**: {재발 방지 방법}

---

### 3.2 자주 발생하는 문제 (Q&A)

**Q: {질문}**
A: {답변}

**Q: {질문}**
A: {답변}

---

## 4. 모니터링 및 알람

### CloudWatch 핵심 지표

| 지표 | 네임스페이스 | 의미 | 임계값 예시 |
|------|-------------|------|------------|
| `{MetricName}` | `{Namespace}` | {설명} | `> {threshold}` |

### 알람 설정

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "{SERVICE}-{condition}-alarm" \
  --alarm-description "{설명}" \
  --metric-name "{MetricName}" \
  --namespace "{Namespace}" \
  --statistic Average \
  --period 300 \
  --evaluation-periods 2 \
  --threshold {value} \
  --comparison-operator GreaterThanThreshold \
  --alarm-actions "arn:aws:sns:ap-northeast-2:123456789012:<SNS_TOPIC>" \
  --region ap-northeast-2
```

---

## 5. TIP

- {현장 유용 팁 1}
- {현장 유용 팁 2}

**관련 문서**
- [AWS 공식 문서]({URL})
- 연관 내부 문서: `docs/{category}/{related-file}.md`
