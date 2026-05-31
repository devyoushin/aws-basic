# 트러블슈팅 인덱스

운영 중 실제로 겪은 증상 → 원인 → 해결 순서로 기록합니다.
파일명 패턴: `{서비스}-{증상}.md`

---

## 증상별 빠른 탐색

| 증상 | 서비스 | 파일 |
|------|--------|------|
| 멀티홉 구조 Backend 간헐적 Timeout — UTM에서 SYN만 관찰됨 | NLB/UTM/TGW/ALB/APIGW/EKS+Istio | [network-backend-timeout-syn-utm.md](network-backend-timeout-syn-utm.md) |
| Pod가 OOMKilled로 계속 재시작됨 | EKS | `eks-pod-oomkilled.md` |
| ImagePullBackOff — ECR 이미지 못 가져옴 | EKS / ECR | `eks-imagepullbackoff.md` |
| EBS BurstBalance 소진 — I/O 레이턴시 급등 | EC2 / EBS | `ec2-ebs-burst-exhausted.md` |
| RDS 연결 수 초과 — Too many connections | RDS | `rds-connection-pool-exhausted.md` |
| BGP 세션 Flapping — DX 경유 트래픽 단절 | Direct Connect | `dx-bgp-flapping.md` |

---

## 파일 추가 가이드

1. 파일명: `{서비스}-{증상-키워드}.md` (소문자, 하이픈 구분)
2. 위 테이블에 행 추가
3. 문서 구조:
   - **증상** — 어떤 알람/에러가 발생했는가
   - **원인** — 근본 원인 (단순 증상 나열 금지)
   - **해결** — 재현 가능한 CLI/Terraform 명령
   - **재발 방지** — CloudWatch 알람, 예방 설정
