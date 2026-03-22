## 1. 개요
**IMDS(Instance Metadata Service)**는 EC2 인스턴스 내부에서 실행 중인 소프트웨어가 인스턴스의 이름, IP, IAM 역할(Role) 등의 정보를 얻기 위해 사용하는 서비스입니다.
과거의 v1(Request/Response) 방식에서 발생하던 보안 취약점을 해결하기 위해 세션 기반의 v2(Session-oriented) 방식이 도입되었습니다.

## 2. 설명
### 2.1 주요 차이점 분석
IMDSv2는 단순한 업데이트가 아니라 인증 절차를 추가하여 보안성을 강화한 버전입니다.

| 구분 | IMDSv1 | IMDSv2 |
| :--- | :--- | :--- |
| **통신 방식** | 단순 HTTP GET 요청 | **세션 기반 (PUT → GET)** |
| **인증 수단** | 없음 (비인증) | **Session Token** 발급 및 헤더 포함 |
| **주요 방어** | 보안 설정 없음 | **SSRF(서버 측 요청 위조)** 공격 차단 |
| **HTTP 헤더** | 불필요 | `X-aws-ec2-metadata-token` 필수 |

### 2.2 왜 IMDSv2인가?
IMDSv2는 **SSRF(Server-Side Request Forgery)** 취약점을 이용한 공격자가 인스턴스의 자격 증명(Credentials)을 탈취하는 것을 방어하기 위해 설계되었습니다. 
토큰을 생성하는 과정에서 `PUT` 메소드와 특정 HTTP 헤더를 요구함으로써, 단순한 URL 호출만으로는 메타데이터에 접근할 수 없도록 차단합니다.

## 3. 트러블 슈팅 (Troubleshooting)
### 3.1 IMDSv2 전환 후 통신 실패
기존 v1 코드를 사용하다가 v2로 강제 전환(Required)하면 401 Unauthorized 에러가 발생합니다.

해결 방법: SDK를 최신 버전으로 업데이트하거나, 아래와 같이 2단계 요청 방식으로 쉘 스크립트를 수정해야 합니다.
```bash
# 1. 토큰 발급
TOKEN=`curl -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600"`

# 2. 토큰을 헤더에 담아 데이터 요청
curl -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/
```

### 3.2 컨테이너 환경(Docker/EKS)에서의 접근 문제
Docker 컨테이너나 Kubernetes Pod에서 IMDSv2 접근이 안 되는 경우가 있습니다.

원인: IMDSv2의 기본 Hop Limit이 1로 설정되어 있어, 네트워크 홉을 한 번 더 거치는 컨테이너 계층에서 패킷이 드롭됩니다.
해결 방법: AWS CLI를 통해 해당 인스턴스의 http-put-response-hop-limit 값을 2 이상으로 상향 조정합니다.

## 4. 참고자료
[AWS 공식 문서: IMDSv2 사용 가이드](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/configuring-instance-metadata-service.html)
