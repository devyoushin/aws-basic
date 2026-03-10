## 1. 개요
**AWS Direct Connect (DX)** 환경에서 단일 장애점(SPOF)을 제거하고 비즈니스 연속성을 보장하기 위한 **회복탄력성(Resiliency) 및 고가용성 아키텍처** 구성 가이드입니다.

## 2. 설명
* **고가용성 모델 (High Resiliency):** 99.9% SLA를 제공하며, 하나의 AWS DX 로케이션에서 2개의 개별 물리적 연결(Connection)을 프로비저닝하여 라우터/스위치 장애에 대비합니다.
* **최대 복원력 모델 (Maximum Resiliency):** 99.99% SLA를 제공하며, 2개의 서로 다른 DX 로케이션(예: KINX와 KDN)에 각각 2개씩, 총 4개의 물리적 연결을 구성하여 로케이션 자체의 장애까지 대비합니다.
* **VPN 백업:** 비용 문제로 DX 회선을 추가하기 어렵다면, AWS Site-to-Site VPN을 백업 경로로 구성하여 BGP 우선순위(AS_PATH 등)를 통해 장애 시 VPN으로 우회하도록 설정할 수 있습니다.

## 3. 트러블 슈팅
* **장애 조치(Failover) 지연:**
  * 기본 회선 장애 시 백업 회선으로 트래픽이 넘어가는 데 시간이 오래 걸린다면, BGP 라우터의 **BFD (Bidirectional Forwarding Detection)** 기능이 활성화되어 있는지 확인해야 합니다. BFD를 사용하면 밀리초(ms) 단위로 링크 장애를 감지할 수 있습니다.
* **비대칭 라우팅 (Asymmetric Routing):**
  * 여러 DX 회선을 사용할 때 나가는 트래픽과 들어오는 트래픽의 경로가 달라져 온프레미스 방화벽에서 패킷이 차단될 수 있습니다. BGP의 Local Preference나 AS_PATH Prepending을 사용하여 주/부 경로를 명확히 설계해야 합니다.

## 4. 참고자료 또는 링크
* [AWS 공식 문서 - Direct Connect 복원력 권장 사항](https://aws.amazon.com/ko/directconnect/resiliency-recommendation/)
- [[참고] dx 고가용성 설계 참고 자료](https://aws.amazon.com/ko/blogs/networking-and-content-delivery/building-resiliency-for-aws-direct-connect-maintenance-events-to-mitigate-downtime/)
