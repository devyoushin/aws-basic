# AGENTS.md — aws-basic Codex 작업 지침

이 저장소는 AWS 운영 지식 베이스입니다. Codex 작업 시 `CLAUDE.md`와 `docs/rules/`의 규칙을 동일하게 따릅니다.

## 1) 공용 원칙 (필수)

- **기본 언어**: 한국어, 기술 용어는 첫 등장 시 영어 원문 병기
- **재현 가능성**: CLI/Terraform/boto3 예시는 복붙 즉시 실행 가능 수준으로 작성
- **근본 원인 중심**: 트러블슈팅은 증상→원인→해결 순서로 기술
- **모니터링 포함**: 가능한 경우 CloudWatch 지표/알람까지 포함
- **추측 금지**: 불확실한 표현 금지, 숫자/한도/비용은 근거(공식 문서 링크 등) 없이 단독 기재 금지

세부 규칙(문서 스타일/코드 컨벤션/보안/모니터링):
- `docs/rules/doc-writing.md`
- `docs/rules/aws-conventions.md`
- `docs/rules/security-checklist.md`
- `docs/rules/monitoring.md`

## 2) 디렉터리/파일 규칙

- 지식 문서: `docs/{카테고리}/{서비스}-{주제}.md`
- 템플릿: `docs/templates/` 하위 템플릿을 우선 사용
- 스크립트/예제 코드: `ops/cli/`, `ops/sdk/`, `ops/lambda/` 하위에 목적에 맞게 추가

## 3) Claude와의 싱크

- `CLAUDE.md`: Claude용 프로젝트 지침입니다.
- `AGENTS.md`: Codex용 진입점입니다.
- `CLAUDE.local.md`: 로컬 개인 설정입니다. 팀 규칙/공용 가이드는 여기에 추가하지 않습니다.
- 공통 규칙은 `docs/rules/`를 기준으로 유지합니다.

## 4) 작업 체크리스트 (간단)

- 새 문서면 5개 섹션(개요/설명/트러블슈팅/모니터링 및 알람/TIP) 포함 여부 확인 (`docs/rules/doc-writing.md` 기준)
- 파일명/경로가 네이밍 규칙을 따르는지 확인 (`CLAUDE.md` 참고)
- 보안/권한 관련이면 `docs/rules/security-checklist.md`도 함께 점검

## 5) Git 커밋 메시지 (AI 사용 표기)

Claude/Codex 등 AI 도움을 받았으면 커밋 메시지 **본문 하단 트레일러**에 아래 키로 남깁니다.

예시:

```text
chore: update docs

AI-Assistant: Codex
```

- 키: `AI-Assistant:`
- 값: `Claude` 또는 `Codex` (필요 시 `GPT` 등 확장 가능)
- 권장: 한 커밋에서 한 값만 사용 (혼용이면 `Claude, Codex`처럼 콤마로 병기)
