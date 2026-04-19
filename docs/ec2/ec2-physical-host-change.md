## 1. 개요
AWS 데이터센터 내의 **기본 하드웨어(Physical Host) 장애나 유지보수 이벤트**가 발생했을 때, EC2 인스턴스를 새롭고 건강한 물리적 호스트로 안전하게 이동시키는 방법입니다.

## 2. 설명
* **AWS의 하드웨어 관리:** AWS는 물리적 서버에 문제가 감지되면 고객에게 "EC2 인스턴스 성능 저하(Degraded)" 또는 "예약된 유지보수(Scheduled Maintenance)" 이메일을 발송합니다.
* **물리적 호스트 변경 방법 (Stop & Start):**
  * EBS 기반 인스턴스는 콘솔에서 **중지(Stop)** 후 **시작(Start)**을 누르는 것만으로 물리적 호스트가 변경됩니다.
  * 중지 시 기존 하드웨어에서 인스턴스가 할당 해제되며, 다시 시작할 때 AWS 스케줄러가 **건강한 다른 물리적 하드웨어에 인스턴스를 새롭게 배치**합니다. (단순 '재부팅(Reboot)'은 동일한 하드웨어에서 OS만 재시작하므로 효과가 없습니다.)

## 3. 트러블 슈팅
* **인스턴스 스토어(Instance Store) 데이터 유실:**
  * NVMe 인스턴스 스토어(임시 블록 스토리지)가 장착된 인스턴스를 Stop/Start 하면 **해당 디스크의 데이터는 영구적으로 삭제(초기화)됩니다.** 이동 전 반드시 중요한 데이터를 EBS나 S3로 백업해야 합니다.
* **퍼블릭 IP(Public IP) 변경:**
  * Elastic IP(탄력적 IP)를 연결하지 않은 상태에서 Stop/Start를 수행하면, **새로운 퍼블릭 IP가 무작위로 재할당**됩니다. 도메인이나 방화벽에 IP가 하드코딩되어 있다면 장애가 발생합니다.

## 4. 참고자료 또는 링크
* [AWS Knowledge Center - EC2 인스턴스의 물리적 호스트 변경](https://repost.aws/ko/knowledge-center/ec2-issue-hardware-host)

## TIP
AWS의 hardware fault 발생시 EC2의 instance를 중지 및 재시작 작업을 수행해줘야하는데, 중지 및 재시작을 해도 안바뀌는 경우가 있다. 
그런 경우에는 AWS TAM이나 Support에 case open을 해서 특정시간 작업을 예약해두고 진행하면 된다.

- **Reboot:** 동일한 물리적 호스트 내에서 인스턴스 소프트웨어만 재시작합니다.
- **Stop & Start:** 인스턴스가 중지되면 물리적 호스트와의 연결이 끊깁니다. 다시 `Start`를 누르면 AWS 스케줄러가 해당 가용 영역(AZ) 내에서 가장 건강한 **새로운 물리 서버(Host)**를 찾아 인스턴스를 배치합니다.

- **[참고]EC2 인스턴스 중지 및 시작 (호스트 이관 관련)** \
https://docs.aws.amazon.com/ko_kr/AWSEC2/latest/UserGuide/Stop_Start.html

- **[참고]인스턴스 스토어 데이터 유지 관련 주의사항** \
https://docs.aws.amazon.com/ko_kr/AWSEC2/latest/UserGuide/instance-store-lifetime.html

- **[참고]용량 부족 오류(InsufficientInstanceCapacity) 해결** \
https://docs.aws.amazon.com/ko_kr/AWSEC2/latest/UserGuide/troubleshooting-launch.html

- **[참고]전용 호스트의 인스턴스 선호도 설정** \
https://docs.aws.amazon.com/ko_kr/AWSEC2/latest/UserGuide/dedicated-hosts-overview.html
