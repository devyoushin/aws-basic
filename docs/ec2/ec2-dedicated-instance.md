## 1. 개요
전용 인스턴스(Dedicated Instance)는 단일 테넌트(Single-tenant) 하드웨어, 즉 **다른 AWS 고객과 물리적 서버를 공유하지 않고** 오직 내 AWS 계정 전용으로 할당된 물리적 서버에서 실행되는 EC2 인스턴스입니다.

## 2. 설명
* **도입 목적:** 엄격한 보안 컴플라이언스(의료, 금융 등) 규정을 준수해야 하거나, 물리적으로 완전히 격리된 환경이 필요할 때 사용합니다.
* **Dedicated Host와의 차이:**
  * **Dedicated Instance:** 물리적 서버가 나만의 것이지만, 서버에 대한 가시성(소켓, 코어 수 확인)이나 제어권은 없습니다.
  * **Dedicated Host (전용 호스트):** 물리적 서버 전체를 통째로 임대하여 하드웨어 소켓, 코어에 대한 가시성을 제공합니다. 주로 Windows Server, SQL Server 등 **기존의 물리 코어 기반 라이선스(BYOL)**를 AWS로 가져올 때 필수적으로 사용됩니다.

## 3. 트러블 슈팅
* **인스턴스 테넌시(Tenancy) 변경 불가:**
  * 이미 `Default`(공유 하드웨어)로 생성된 인스턴스를 실행 중에 `Dedicated`로 변경할 수 없습니다. 반대도 불가능합니다. AMI를 생성한 후 새 인스턴스를 런칭할 때 테넌시를 지정해야 합니다.
  * 단, VPC 자체의 기본 테넌시를 `Dedicated`로 만들면 그 안에 생성되는 모든 인스턴스는 전용 인스턴스가 됩니다.
* **예상치 못한 고액 과금:**
  * 전용 인스턴스는 인스턴스 사용 요금 외에도, 해당 리전(Region)에서 전용 인스턴스를 하나라도 실행 중이라면 **시간당 $2의 추가 고정 요금**이 발생합니다. (테스트용으로 켰다가 끄지 않으면 비용 폭탄의 주범이 됩니다.)

## 4. 참고자료 또는 링크
* [AWS 공식 문서 - Amazon EC2 전용 인스턴스](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/dedicated-instance.html)
* [Dedicated instance 사용 참고자료](https://docs.aws.amazon.com/ko_kr/AWSEC2/latest/UserGuide/dedicated-hosts-overview.html)
