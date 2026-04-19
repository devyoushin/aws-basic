---
description: AWS 서비스 신규 문서를 스캐폴딩합니다. 사용법: /new-doc <서비스> <주제>
---

아래 지침에 따라 AWS 지식 베이스 문서를 새로 작성해 주세요.

## 입력 정보
- 사용자 입력: $ARGUMENTS
- 첫 번째 인자 = 서비스 약어 (ec2, eks, cloudwatch, vpc, s3, iam, dx, rds 등)
- 두 번째 인자 이후 = 주제 (하이픈 구분)

## 파일 생성 규칙
1. 파일명: `{서비스}-{주제}.md` (모두 소문자, 단어 구분은 하이픈)
2. 저장 위치: 프로젝트 루트 (`/Users/sunny/Desktop/aws-basic/`)
3. `rules/doc-writing.md` 의 문서 작성 규칙 준수
4. `rules/aws-conventions.md` 의 코드 작성 규칙 준수
5. `rules/security-checklist.md` 의 보안 체크리스트 통과

## 문서 구조 (반드시 아래 섹션 모두 포함)

```markdown
# {주제명}

## 1. 개요
## 2. 설명
### 2.1 핵심 개념
### 2.2 실무 적용 코드
### 2.3 보안/비용 Best Practice
## 3. 트러블슈팅
### 3.1 주요 이슈
### 3.2 자주 발생하는 문제 (Q&A)
## 4. 모니터링 및 알람
## 5. TIP
```

문서 작성 완료 후 `CLAUDE.md`의 카테고리별 파일 목록에 해당 항목을 추가해 주세요.
