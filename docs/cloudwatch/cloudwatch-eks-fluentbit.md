## 1. 개요
**Amazon EKS** 클러스터에서 실행되는 컨테이너들의 로그(stdout/stderr) 및 호스트 로그를 수집하여 **CloudWatch Logs**로 안정적으로 전송하기 위해 가벼운 로그 수집기인 **Fluent Bit**를 구성하는 방법입니다.

## 2. 설명
* **Fluent Bit vs Fluentd:** Fluent Bit는 C언어로 작성되어 매우 가볍고 메모리 사용량이 적어, 쿠버네티스 환경에서 로그 수집의 AWS 권장 표준(Best Practice)으로 사용됩니다.
* **배포 아키텍처:**
  * **DaemonSet:** 클러스터의 모든 워커 노드마다 1개씩 배포되어, 해당 노드 내의 `/var/log/containers/` 경로에 떨어지는 파드 로그를 꼬리물기(Tail) 방식으로 읽어옵니다.
* **기능:** 수집된 로그에 쿠버네티스의 메타데이터(Pod 이름, Namespace, Label 등)를 자동으로 추가(Enrichment)하여 CloudWatch에서 검색하기 쉽게 만들어 줍니다.

## 3. 참조 및 관련된 파일
* [[eks-fargate]] (Fargate 환경에서의 로깅은 별도 설정 필요)
* [[eks-nodeadm]]
* [[cloudwatch-custom-metric]]

## 4. 트러블 슈팅
* **CloudWatch Logs에 로그 그룹(Log Group)이 생성되지 않거나 로그가 안 들어옴:**
  * **IRSA 권한 문제:** Fluent Bit 파드에 연결된 ServiceAccount (IRSA)에 `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents` 권한이 포함된 IAM 정책이 제대로 매핑되었는지 확인합니다.
* **로그 파싱 에러 또는 멀티라인(Multi-line) 에러:**
  * Java Stack Trace처럼 여러 줄로 출력되는 로그가 각 줄마다 별개의 로그로 인식될 수 있습니다. Fluent Bit ConfigMap에서 `multiline.parser` 설정을 애플리케이션 언어(Java, Go 등)에 맞게 구성해야 합니다.

## 5. 참고자료 또는 링크
* [AWS 공식 문서 - EKS용 CloudWatch 관측성(Fluent Bit 설정)](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/Container-Insights-setup-logs-FluentBit.html)
- [참고] https://docs.aws.amazon.com/ko_kr/AmazonCloudWatch/latest/monitoring/Container-Insights-setup-logs-FluentBit.html#Container-Insights-FluentBit-setup
