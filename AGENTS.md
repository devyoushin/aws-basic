# AGENTS.md — aws-basic 에이전트 작업 지침 (Codex/Claude 공용)

이 저장소는 `docs/` 중심의 개인 지식 베이스입니다. Codex/Claude 등 어떤 에이전트로 작업하더라도, 문서 추가/수정 시 본 지침과 `rules/` 규칙을 우선합니다.

## 1) 공용 원칙 (필수)

- **기본 언어**: 한국어, 기술 용어는 첫 등장 시 영어 원문 병기
- **재현 가능성**: CLI/Terraform/boto3 예시는 복붙 즉시 실행 가능 수준으로 작성
- **근본 원인 중심**: 트러블슈팅은 증상→원인→해결 순서로 기술
- **모니터링 포함**: 가능한 경우 CloudWatch 지표/알람까지 포함
- **추측 금지**: 불확실한 표현 금지, 숫자/한도/비용은 근거(공식 문서 링크 등) 없이 단독 기재 금지

세부 규칙(문서 스타일/코드 컨벤션/보안/모니터링):
- `rules/doc-writing.md`
- `rules/aws-conventions.md`
- `rules/security-checklist.md`
- `rules/monitoring.md`

## 2) 디렉터리/파일 규칙

- 지식 문서: `docs/{카테고리}/{서비스}-{주제}.md`
- 템플릿: `templates/` 하위 템플릿을 우선 사용
- 스크립트/예제 코드: `cli/`, `sdk/`, `lambda/` 하위에 목적에 맞게 추가

## 3) Claude 설정과의 공존

- `CLAUDE.md`: 프로젝트 구조/가이드(팀 공용). 본 파일(`AGENTS.md`)은 Codex 관점의 “진입점” 역할을 합니다.
- `CLAUDE.local.md`: 로컬 개인 설정(개인 환경 전용). 팀 규칙/공용 가이드는 여기에 추가하지 않습니다.
- `.claude/`: Claude 워크플로 설정(참고용). Codex는 직접 해석하지 않을 수 있습니다.

## 4) 작업 체크리스트 (간단)

- 새 문서면 5개 섹션(개요/설명/트러블슈팅/모니터링 및 알람/TIP) 포함 여부 확인 (`rules/doc-writing.md` 기준)
- 파일명/경로가 네이밍 규칙을 따르는지 확인 (`CLAUDE.md` 참고)
- 보안/권한 관련이면 `rules/security-checklist.md`도 함께 점검

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
