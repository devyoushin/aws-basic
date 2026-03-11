## 1. 개요
- 현상: 특정 새벽 시간대에 AWS Direct Connect(DX)의 BGP 세션과 VIF(가상 인터페이스) 상태가 동시에 Down으로 전환됨.
- 특이사항: 인프라 모니터링상 장애는 감지되었으나, 서비스 부서(Application/Service Team)로부터의 장애 인입이나 고객 민원은 발생하지 않음.
- 목적: 장애 발생 원인을 파악하고, 서비스 영향이 없었던 이유(이중화 작동 여부 등)를 검토하여 재발 방지 대책 수립.

## 2. 설명

### VIF(Virtual Interface) Down의 의미
- VIF는 물리적인 DX 회선 위에서 생성되는 논리적인 통로입니다. VIF가 Down 되었다는 것은 해당 논리적 경로 자체가 폐쇄되었음을 의미하며, 이는 보통 물리 회선 이슈, VLAN 설정 오류, 또는 AWS/IDC 측의 장비 점검 시 발생합니다.

### BGP 세션과의 상관관계
- BGP는 VIF라는 통로를 통해 라우팅 정보를 주고받는 프로토콜입니다. 따라서 **VIF가 Down되면 그 위에서 동작하던 BGP 세션은 즉시 중단(Down)**됩니다. 이는 네트워크 하위 계층(L2)의 문제가 상위 계층(L3)으로 전이된 전형적인 사례입니다.

### 서비스 영향이 없었던 가설
1. 회선 이중화(Active-Standby/Active-Active): DX가 2회선으로 구성되어 있어, 장애가 발생한 회선 대신 백업 회선으로 트래픽이 즉시 우회됨.
2. 새벽 시간대 트래픽 부재: 서비스 이용자가 거의 없는 시간대라 에러율(Error Rate) 임계치를 넘지 않음.
3. 캐싱(Caching): 클라이언트나 중간 프록시 단에서 DNS 또는 세션 캐싱이 작동하여 짧은 순단은 무시됨.

## 3. 트러블 슈팅
장애 당시 상황을 복기하기 위해 다음 단계를 수행해야합니다.

### VIF 상태 로그 확인:
- AWS 콘솔 Direct Connect > Virtual Interfaces에서 해당 VIF의 상태 이력을 확인합니다.
- Down 상태가 얼마나 지속되었는지, 다시 Available로 복구된 시점은 언제인지 체크합니다.

### BGP Peer 상태 분석:
- 장비 로그에서 BGP 이웃(Neighbor)과의 연결 끊김 원인(예: Interface Down, Hold Timer Expired)을 파악합니다.

### AWS Personal Health Dashboard(PHD) 대조:
- 장애 발생 시간대에 해당 DX 로케이션(Location)의 긴급 점검이나 네트워크 장비 패치 작업이 있었는지 공지사항을 확인합니다.

### 트래픽 경로(As-Is/To-Be) 분석:
- CloudWatch의 VirtualInterfaceBps 지표를 통해 장애 시점에 다른 VIF로 트래픽이 정상적으로 옮겨갔는지 검증합니다.

## 4. 참고 자료 또는 링크
- AWS 공식 문서: [Direct Connect 연결 상태 문제 해결](https://repost.aws/ko/knowledge-center/troubleshoot-bgp-dx)
- BGP Troubleshooting 가이드: [Cisco BGP Neighbor States 설명](https://www.cisco.com/c/ko_kr/support/docs/ip/border-gateway-protocol-bgp/218027-troubleshoot-border-gateway-protocol-bas.html)
- CloudWatch Metrics: https://docs.aws.amazon.com/ko_kr/directconnect/latest/UserGuide/monitoring-cloudwatch.html
- 참고: https://aws.amazon.com/ko/blogs/networking-and-content-delivery/monitor-bgp-status-on-aws-direct-connect-vifs-and-track-prefix-count-advertised-over-transit-vif/
- 참고: https://repost.aws/ko/knowledge-center/direct-connect-connectivity-issues
- 참고: [Direct Connect 가상 인터페이스(VIF) 유형 및 상태 관리](https://repost.aws/ko/knowledge-center/direct-connect-down-virtual-interface)
- 참고: [Direct Connect VIF가 Down되었을 때 해결 방법](https://docs.aws.amazon.com/ko_kr/directconnect/latest/UserGuide/WorkingWithVirtualInterfaces.html)
- 참고: [Direct Connect 가용성 및 BGP 모니터링 모범 사례](https://aws.amazon.com/ko/blogs/networking-and-content-delivery/monitor-bgp-status-on-aws-direct-connect-vifs-and-track-prefix-count-advertised-over-transit-vif/)

## TIP
- BFD(Bidirectional Forwarding Detection) 설정 권장: BGP는 장애 감지 속도가 기본적으로 느립니다(보통 180초). BFD를 활성화하면 초 단위로 장애를 감지하여 즉시 Failover를 유도할 수 있습니다.
- 알람 임계치 조정: 이번처럼 서비스 부서가 모를 정도로 짧은 장애라면, 알람의 발생 빈도나 지속 시간 조건을 재검토하여 '노이즈'성 알람인지 '필수 대응' 알람인지 구분하십시오.
- 정기 점검 시간 공지: 새벽 시간 점검은 ISP나 AWS에서 흔히 발생합니다. 관련 메일링 리스트를 수시로 확인하여 "예고된 장애"였는지 파악하는 습관이 중요합니다.
