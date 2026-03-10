## 1. 개요
AWS Direct Connect 구간 또는 그 너머의 네트워크에서 **패킷 손실(Packet Loss)** 현상이 발생할 때의 원인 분석 및 해결 방법입니다.

## 2. 설명
* **패킷 손실의 주요 원인:**
  * **대역폭 초과 (Bandwidth Saturation):** 구매한 DX 포트 속도(예: 1Gbps)를 초과하는 트래픽이 발생하여 스위치에서 패킷을 드롭(Drop)하는 경우.
  * **마이크로버스트 (Microbursts):** CloudWatch 1분 평균 트래픽은 낮아 보이지만, 밀리초 단위로 한꺼번에 트래픽이 몰려 스위치 버퍼가 꽉 차는 현상.
  * **온프레미스 장비/방화벽 문제:** 라우팅 오류, 방화벽의 세션 초과 또는 IPS(침입 방지 시스템)에 의한 오탐 차단.
  * **물리적 오류:** 광신호 약화(MAC 계층 에러).

## 3. 트러블 슈팅
* **문제 구간 식별 (MTR / Traceroute):**
  * 양방향으로 `mtr` 또는 `traceroute` 명령어를 실행하여 어느 홉(Hop)에서부터 패킷 손실이나 지연(Latency)이 발생하는지 정확히 파악해야 합니다.
* **iPerf 대역폭 테스트:**
  * 애플리케이션 문제인지 네트워크 문제인지 격리하기 위해, 온프레미스 서버와 AWS EC2 간에 `iperf3` 툴을 사용하여 순수한 TCP/UDP 네트워크 대역폭 및 패킷 로스 테스트를 진행합니다.
* **CloudWatch ConnectionErrorCount 확인:**
  * 물리 계층에서 MAC 수준 에러가 발생하는지 확인합니다. 이 수치가 높다면 AWS 서포트에 케이블/포트 점검을 요청해야 합니다.

## 4. 참고자료 또는 링크
* [AWS Knowledge Center - Direct Connect 패킷 손실 해결](https://repost.aws/ko/knowledge-center/direct-connect-packet-loss)
- [참고]dx packet loss 참고 자료
https://repost.aws/knowledge-center/direct-connect-packet-loss
