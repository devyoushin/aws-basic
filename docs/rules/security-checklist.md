# 보안 검토 체크리스트 (Security Review Checklist)

문서 작성 또는 코드 예시 추가 전 아래 항목을 반드시 확인합니다.

---

## 1. 코드 보안

| 항목 | 확인 |
|------|------|
| 하드코딩된 AWS Access Key / Secret Key 없음 | ☐ |
| 하드코딩된 계정 ID 없음 (플레이스홀더 사용) | ☐ |
| 실제 ARN, 리소스 ID 없음 | ☐ |
| 패스워드, 토큰, API Key 없음 | ☐ |
| `0.0.0.0/0` 인바운드 허용 시 주의 문구 포함 | ☐ |

## 2. IAM 정책 검토

- `"Effect": "Allow"` + `"Action": "*"` + `"Resource": "*"` 조합 금지
  - 필요 시 반드시 `Condition` 추가 + 이유 주석
- `sts:AssumeRole` Trust Policy에 `aws:MultiFactorAuthPresent` 조건 권장
- Cross-account 설정 시 External ID 사용 권장

## 3. 네트워크 보안

- Security Group 인바운드 예시에서 `0.0.0.0/0` 사용 시:
  ```
  # 주의: 프로덕션에서는 특정 IP CIDR로 제한 필요
  ```
- NACLs는 Security Group의 보조 수단임을 명시

## 4. 데이터 보안

- S3 버킷 예시에서 `ACL = "public-read"` 사용 금지
- EBS 암호화: `encrypted = true` 기본 포함
- RDS 예시: `storage_encrypted = true` 기본 포함
- KMS 키 예시에서 `enable_key_rotation = true` 기본 포함

## 5. 로깅 및 감사

새 서비스 문서 작성 시 아래 포함 여부 확인:
- CloudTrail 이벤트 소스 명시
- 관련 서비스 로그 활성화 방법
- 이상 탐지를 위한 CloudWatch Alarm 예시
