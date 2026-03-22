
# 1. 개요 (Introduction)

AWS 환경에서 **SHA-256(Secure Hash Algorithm 256-bit)**은 데이터의 '지문' 역할을 수행하는 핵심 알고리즘입니다. 클라우드 엔지니어에게 SHA-256은 단순히 암호학적 개념을 넘어, **IAC(Terraform)의 상태 관리, S3 데이터 무결성 검증, 그리고 Lambda 코드의 변경 감지**를 위한 필수 도구입니다.

---

# 2. 설명 (Explanation)

### 2.1 AWS에서 SHA-256을 사용하는 주요 이유

1. **무결성 검증 (Integrity):** 데이터가 전송 중이나 저장 중에 변조되지 않았음을 보장합니다.
2. **고유 식별 (Unique Identifier):** Terraform 등에서 리소스의 변경 사항을 감지할 때, 전체 데이터를 비교하는 대신 Hash 값을 비교하여 효율성을 높입니다.
3. **충돌 저항성 (Collision Resistance):** 서로 다른 두 데이터가 동일한 SHA-256 값을 가질 확률은 극히 희박하여 보안상 안전합니다.

### 2.2 실무 적용 코드

#### A. Terraform: Lambda 소스 코드 변경 감지

Terraform은 `source_code_hash` 필드에서 SHA-256을 사용하여 배포 패키지의 변경 여부를 판단합니다.

```terraform
resource "aws_lambda_function" "my_lambda" {
  filename      = "lambda_function_payload.zip"
  function_name = "process_data_func"
  role          = aws_iam_role.iam_for_lambda.arn
  handler       = "index.handler"

  # 파일의 SHA256 해시를 계산하여 변경 시에만 재배포 수행
  source_code_hash = filebase64sha256("lambda_function_payload.zip")

  runtime = "nodejs18.x"
}
```

#### B. AWS CLI/S3: 업로드 객체 무결성 확인

S3에 대용량 파일을 올릴 때 `Content-SHA256` 헤더를 사용하여 업로드 중 데이터 손실이나 변조를 방지합니다.


```bash
# 로컬 파일의 SHA256 값 계산 (Linux/macOS)
shasum -a 256 my-large-video.mp4

# S3 업로드 시 체크섬 명시 (SDK나 API 수준에서 주로 활용)
aws s3api put-object --bucket my-bucket --key data.zip --body data.zip --checksum-algorithm SHA256
```

---

# 3. 트러블슈팅 (Troubleshooting)

### Q: Terraform 실행 시 코드를 수정하지 않았는데 Lambda가 계속 업데이트됩니다.

- **원인:** `source_code_hash`에 사용되는 zip 파일 생성 시, 파일의 생성 시간(Timestamp)이나 권한 설정이 포함되어 매번 다른 Hash 값을 생성하기 때문입니다.
- **해결:** 빌드 스크립트에서 `zip` 명령 시 `-X` 옵션을 사용하여 타임스탬프를 제외하거나, 결정론적(Deterministic) 빌드 도구를 사용하세요.

### Q: S3 Checksum 오류 (BadDigest) 발생

- **원인:** 클라이언트에서 계산한 SHA-256 값과 S3가 수신 후 계산한 값이 일치하지 않습니다. 네트워크 전송 중 패킷 오염이 발생했을 가능성이 높습니다.
- **해결:** `aws s3 sync`를 사용하면 자동으로 체크섬을 관리해주며, 수동 업로드 시에는 `Multipart Upload`를 사용하여 각 파트별로 검증해야 합니다.

---

# 4. 모니터링 및 보안/비용 전략

### 4.1 모니터링 및 알람 (Monitoring & Alerting)

- **CloudTrail 감시:** `PutObject` 시 체크섬 검증에 실패한 로그를 CloudWatch Metric Filter로 추적합니다.
- **Metric:** `ValidationErrors`가 특정 임계치를 넘으면 Slack 알람을 발송하여 배포 파이프라인의 오염을 감지합니다.

### 4.2 보안 Best Practice (Security)

- **Signature Version 4 (SigV4):** AWS API 요청은 SHA-256을 사용하여 서명됩니다. 항상 최신 SDK를 사용하여 서명 프로세스의 보안성을 유지하세요.
- **HMAC 활용:** 시스템 간 메시지 전송 시 SHA-256 기반의 HMAC을 사용하여 송신처의 진위 여부를 확인하세요.

### 4.3 비용 최적화 (Cost)

- **Compute Savings:** 모든 데이터에 대해 매번 전체 Hash를 계산하는 것은 CPU 집약적인 작업입니다. 대용량 파일의 경우 `Multipart Upload`의 각 파트 해시를 조합하는 방식을 사용하여 연산 비용을 최적화하세요.
- **Storage 효율:** 중복 데이터를 체크할 때 SHA-256 Hash를 인덱스로 활용하면 중복 저장(Deduplication)을 방지하여 S3 비용을 절감할 수 있습니다.

---


# 5. TIP

### 참고
-  **[AWS General Reference]** [Authenticating Requests (AWS Signature Version 4)](https://docs.aws.amazon.com/general/latest/gr/sigv4_signing.html)
- **[Amazon S3 User Guide]** [Checking object integrity using SHA-256 and other checksums](https://docs.aws.amazon.com/AmazonS3/latest/userguide/checking-object-integrity.html)
- **[AWS Security Blog]** [How to use SHA-256 for data validation in transit](https://www.google.com/search?q=https://aws.amazon.com/blogs/aws/new-additional-checksum-algorithms-for-amazon-s3-objects/)
- **AWS CLI 팁:** 최근 AWS CLI는 `--checksum-algorithm` 옵션을 지원하여 별도의 로컬 스크립트 없이도 SHA-256 무결성 검증을 네이티브하게 지원합니다.
- **알고리즘 선택:** SHA-1이나 MD5는 이미 충돌 취약점이 발견되었으므로, 규제 준수(Compliance)가 필요한 프로젝트라면 반드시 **SHA-256** 이상을 사용해야 합니다.
