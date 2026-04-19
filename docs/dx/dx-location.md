## 1. 개요
**AWS DX 로케이션(Location)** 은 고객의 온프레미스 네트워크 장비와 AWS 글로벌 네트워크가 물리적으로 만나는 상면(Colocation) 시설을 의미합니다.

## 2. 설명
- **개념:** DX 로케이션은 AWS 리전(Region) 데이터센터 자체가 아닙니다. AWS 라우터가 입주해 있는 외부 파트너 데이터센터(예: 한국의 KINX, KDN, LG U+)입니다.
- **크로스 커넥트 (Cross-Connect):** 고객 라우터와 AWS 라우터를 광케이블로 직접 연결하는 작업입니다.
- **LOA-CFA (Letter of Authorization and Connecting Facility Assignment):** AWS에서 DX 연결 생성을 요청하면 발급해 주는 일종의 '작업 허가서'입니다. 이 문서를 로케이션 파트너(데이터센터 관리자)에게 전달해야 물리적 케이블 연결 작업을 진행할 수 있습니다.

## 3. 트러블 슈팅
- **LOA-CFA 다운로드 불가 또는 만료:**
  - 연결 상태가 `ordering`에서 `available`로 넘어가기 전에 LOA-CFA를 다운로드해야 합니다. 발급 후 90일이 지나면 만료되므로 기간 내에 케이블링 작업을 완료해야 합니다.
- **포트 상태가 DOWN인 경우 (물리적 연결 문제):**
  - 데이터센터 측에서 케이블링을 완료했다고 하나 포트가 올라오지 않는다면, 광케이블의 **Tx/Rx(송수신) 가닥이 바뀌었거나(Crossed)**, 광신호 세기(Light Level)가 너무 약한 것이 원인일 수 있습니다. 데이터센터 측에 롤오버(Rollover) 테스트를 요청하세요.

## 4. 참고자료 또는 링크
[AWS 공식 문서 - Direct Connect 위치](https://aws.amazon.com/ko/directconnect/features/#Locations_and_Pricing)

# TIP
- Digital Realty ICN10, Seoul, South Korea
- KINX, Seoul, South Korea
- LG U+ Pyeong-Chon Mega Center, Seoul, South Korea
