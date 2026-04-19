## 1. 개요
**Amazon CloudWatch**를 활용하여 Direct Connect 물리적 연결(Connection) 및 가상 인터페이스(VIF)의 상태와 성능 지표를 모니터링하는 방법입니다.

## 2. 설명
* **주요 Connection 지표:**
  * `ConnectionState`: 물리적 링크의 Up(1)/Down(0) 상태.
  * `ConnectionLightLevelTx` / `ConnectionLightLevelRx`: 물리적 광케이블의 송수신 신호 강도 (dBm).
* **주요 VIF(가상 인터페이스) 지표:**
  * `VirtualInterfaceBpsTx` / `VirtualInterfaceBpsRx`: 초당 전송/수신 비트 수 (대역폭 사용량).
  * `VirtualInterfaceState`: BGP 세션의 Up/Down 상태.
* **알람(Alarm) 구성 필수:** 핵심 비즈니스망인 만큼 `ConnectionState`가 0이 되거나 대역폭 사용량이 80%를 넘을 때 즉각적인 알림(SNS)이 오도록 구성해야 합니다.

## 3. 트러블 슈팅
* **물리적 링크는 UP인데 BGP 세션은 DOWN인 경우:**
  * `ConnectionState`는 1이지만 `VirtualInterfaceState`가 0이라면, 케이블(물리 계층)은 정상이지만 라우팅(네트워크 계층) 설정에 문제가 있는 것입니다. 양측 라우터의 BGP ASN, BGP Password(MD5), IP 주소가 정확히 일치하는지 확인하세요.
* **Light Level이 지속적으로 낮아지는 현상 (Degradation):**
  * `ConnectionLightLevelRx` 값이 권장 범위(-14.4 dBm 이하 등)를 벗어나 낮아진다면 광트랜시버(SFP) 노후화나 케이블 꺾임 등의 물리적 장애 전조 증상일 수 있습니다. 선제적 점검이 필요합니다.

## 4. 참고자료 또는 링크
* [AWS 공식 문서 - CloudWatch를 사용한 Direct Connect 모니터링](https://docs.aws.amazon.com/directconnect/latest/UserGuide/monitoring-cloudwatch.html)
- [참고] Amazon CloudWatch Network Synthetic Monitor
https://docs.aws.amazon.com/ko_kr/AmazonCloudWatch/latest/monitoring/what-is-network-monitor.html

- [참고] # Amazon CloudWatch Network Synthetic Monitor 소개 참고자료
https://aws.amazon.com/ko/blogs/networking-and-content-delivery/monitor-hybrid-connectivity-with-amazon-cloudwatch-network-monitor/
