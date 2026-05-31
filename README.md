# aws-basic

AWS 운영 경험을 바탕으로 EC2, EKS, 네트워크, 보안, 비용, 관측, 자동화 예제를 정리한 개인 지식 베이스입니다.

## 어디서 시작할까

- 문서 지도: `docs/README.md`
- 운영/실습 자산: `ops/README.md`
- AI 작업 지침: `CLAUDE.md`, `AGENTS.md`

## 구조

| 경로 | 내용 |
|------|------|
| `docs/` | AWS 서비스별 지식 문서, 에이전트, 작성 규칙, 템플릿 |
| `ops/` | AWS CLI 스크립트, boto3 SDK 예제, Lambda 함수 예제 |
| `.claude/` | Claude Code 커맨드와 설정 |
| `CLAUDE.md` | Claude 작업 지침 |
| `AGENTS.md` | Codex/agent 작업 지침 |

## 학습 흐름

1. `docs/README.md`에서 서비스별 문서 위치 확인
2. `docs/ec2/`, `docs/eks/`, `docs/network/`, `docs/security/` 순서로 핵심 운영 문서 학습
3. `ops/cli/`와 `ops/sdk/`에서 조회/진단 자동화 예제 확인
4. `ops/lambda/`에서 운영 자동화 함수 예제 확인
