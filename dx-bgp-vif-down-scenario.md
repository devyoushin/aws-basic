## 1. 개요
- 현상: 특정 새벽 시간대에 AWS Direct Connect(DX)의 BGP 세션과 VIP(Virtual IP) 서비스가 동시에 중단됨.
- 특이사항: 인프라 모니터링상 장애는 감지되었으나, 서비스 부서(Application/Service Team)로부터의 장애 인입이나 고객 민원은 발생하지 않음.
- 목적: 장애 발생 원인을 파악하고, 서비스 영향이 없었던 이유(이중화 작동 여부 등)를 검토하여 재발 방지 대책 수립.

## 2. 설명

### 2.1 BGP(Border Gateway Protocol) Down의 의미
- BGP는 고객사 IDC와 AWS 간의 라우팅 정보를 교환하는 프로토콜입니다. BGP가 'Down' 되었다는 것은 두 지점 간의 논리적 통로가 끊겼음을 의미하며, AWS로 가는 경로 정보를 잃어버리게 됩니다.

### 2.2 VIP(Virtual IP) 연동 관계
- 보통 DX 환경에서 VIP는 서비스의 엔드포인트 역할을 합니다. BGP가 끊기면 해당 VIP로 가기 위한 라우팅 경로(Route Table)가 삭제되므로, 외부나 내부에서 해당 IP로 접근할 수 없는 상태가 됩니다.

### 2.3 서비스 영향이 없었던 가설
1. 회선 이중화(Active-Standby/Active-Active): DX가 2회선으로 구성되어 있어, 장애가 발생한 회선 대신 백업 회선으로 트래픽이 즉시 우회됨.
2. 새벽 시간대 트래픽 부재: 서비스 이용자가 거의 없는 시간대라 에러율(Error Rate) 임계치를 넘지 않음.
3. 캐싱(Caching): 클라이언트나 중간 프록시 단에서 DNS 또는 세션 캐싱이 작동하여 짧은 순단은 무시됨.

## 3. 트러블 슈팅
장애 당시 상황을 복기하기 위해 다음 단계를 수행해야합니다.

### 3.1 물리 계층 확인 (L1/L2):
  - 장비 로그(Syslog)에서 Interface Down/Up 이력이 있는지 확인합니다.
  - 광신호 레벨(Tx/Rx Power)이 정상 범위를 벗어났는지 체크합니다.

### 3.2 BGP 상태 코드 분석:
  - Idle, Active, Connect 중 어떤 상태에 머물러 있었는지 확인합니다.
  - Hold Timer Expired 로그가 있다면 네트워크 혼잡이나 중간 구간의 패킷 드롭을 의심해야 합니다.

### 3.3 AWS Personal Health Dashboard(PHD) 대조:
  - 장애 시간대에 해당 DX Location의 점검 공지나 AWS 인프라 이슈가 있었는지 확인합니다.

### 3.4 라우팅 테이블 전파 확인:
  - VPC Route Table에서 DX를 통해 들어오던 경로 정보가 Blackhole 상태였는지, 아니면 정상적으로 보조 경로로 전환되었는지 확인합니다.

## 4. 참고 자료 또는 링크
- AWS 공식 문서: [Direct Connect 연결 상태 문제 해결](https://repost.aws/ko/knowledge-center/troubleshoot-bgp-dx)
- BGP Troubleshooting 가이드: [Cisco BGP Neighbor States 설명](https://www.cisco.com/c/ko_kr/support/docs/ip/border-gateway-protocol-bgp/218027-troubleshoot-border-gateway-protocol-bas.html)
- CloudWatch Metrics: https://docs.aws.amazon.com/ko_kr/directconnect/latest/UserGuide/monitoring-cloudwatch.html
- 참고: https://aws.amazon.com/ko/blogs/networking-and-content-delivery/monitor-bgp-status-on-aws-direct-connect-vifs-and-track-prefix-count-advertised-over-transit-vif/
- 참고: https://repost.aws/ko/knowledge-center/direct-connect-connectivity-issues

## TIP
- BFD(Bidirectional Forwarding Detection) 설정 권장: BGP는 장애 감지 속도가 기본적으로 느립니다(보통 180초). BFD를 활성화하면 초 단위로 장애를 감지하여 즉시 Failover를 유도할 수 있습니다.
- 알람 임계치 조정: 이번처럼 서비스 부서가 모를 정도로 짧은 장애라면, 알람의 발생 빈도나 지속 시간 조건을 재검토하여 '노이즈'성 알람인지 '필수 대응' 알람인지 구분하십시오.
- 정기 점검 시간 공지: 새벽 시간 점검은 ISP나 AWS에서 흔히 발생합니다. 관련 메일링 리스트를 수시로 확인하여 "예고된 장애"였는지 파악하는 습관이 중요합니다.
