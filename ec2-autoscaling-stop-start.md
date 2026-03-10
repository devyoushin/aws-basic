## 1. 개요
**Auto Scaling Group(ASG)** 내에 있는 특정 EC2 인스턴스를 유지보수나 트러블슈팅 목적으로 잠시 중지(Stop)했다가 다시 시작(Start)할 때, ASG의 자동 복구 메커니즘과 충돌하지 않도록 처리하는 방법입니다.

## 2. 설명
* **ASG의 기본 동작:** ASG는 인스턴스가 멈추면(Stop) 이를 '비정상(Unhealthy)' 상태로 간주하고 즉시 해당 인스턴스를 종료(Terminate)한 뒤 새 인스턴스를 띄워버립니다.
* **유지보수 방법 (Standby 상태):**
  * 인스턴스를 중지하기 전, ASG 콘솔이나 CLI에서 해당 인스턴스의 상태를 **`InService`에서 `Standby`(대기) 상태로 변경**해야 합니다.
  * `Standby` 상태가 된 인스턴스는 ASG의 Health Check 대상에서 제외되며 로드 밸런서(ELB) 트래픽도 받지 않게 되어 안전하게 Stop/Start 및 점검을 수행할 수 있습니다. 작업 완료 후 다시 `InService`로 되돌립니다.
* **프로세스 일시 중지 (Suspend Process):** ASG 전체의 동작을 멈추고 싶다면 `HealthCheck`, `Terminate`, `Launch` 등의 프로세스를 개별적으로 일시 중지할 수 있습니다.

## 3. 트러블 슈팅
* **Standby 전환 중 용량 부족 에러:**
  * 인스턴스를 Standby로 내리면 활성 용량이 줄어들기 때문에, ASG가 '원하는 용량(Desired Capacity)'을 맞추기 위해 새 인스턴스를 추가로 띄울 수 있습니다. 이를 방지하려면 Standby 전환 시 "원하는 용량 감소(Decrement desired capacity)" 옵션을 선택해야 합니다.
* **수동으로 Stop한 인스턴스가 Terminate 됨:**
  * 위의 예방 조치(Standby 또는 스케일인 보호) 없이 콘솔에서 무작정 `인스턴스 중지`를 누른 경우 발생하는 정상적인(의도된) 동작입니다. 로그 백업이 필요했다면 이미 삭제되었을 확률이 높습니다.

## 4. 참고자료 또는 링크
* [AWS 공식 문서 - Auto Scaling 그룹에서 인스턴스 일시 중지 및 재개](https://docs.aws.amazon.com/autoscaling/ec2/userguide/as-suspend-resume-processes.html)


## TIP

**오토스케일링으로 구성된 EC2의 중지 및 시작 작업시에 주의해야하는 점**
-> ASG그룹에 할당되어있는 인스턴스는 곧바로 재기동 또는 중지 및 시작 작업을 진행할때 바로 인스턴스 중지를 진행하면 안 된다.  아래의 체크 사항을 참고하여 진행하면 좋음.
1. 인스턴스 축소보호: 콘솔>EC2>Auto Scaling>Auto Scaling 그룹>[ASG 선택]>인스턴스 관리>인스턴스 모두 선택> 인스턴스 축소보호 설정 선택
2. Min 수정: 콘솔>EC2>Auto Scaling>Auto Scaling 그룹>[ASG 선택]>용량개요>편집>원하는 최소용량:[개수 선택]>업데이트
3. 대기상테: 콘솔>EC2>Auto Scaling>Auto Scaling 그룹>[ASG 선택]>인스턴스 관리>[인스턴스 선택]>작업>대기로 설정>인스턴스 교체 해제 체크>상태확인:Entering Standby->Standby (복구시에는 InService)

- **[참고]인스턴스를 대기(Standby) 상태로 설정**
https://docs.aws.amazon.com/ko_kr/autoscaling/ec2/userguide/as-enter-exit-standby.html

- **[참고]인스턴스 축소 보호**
https://docs.aws.amazon.com/ko_kr/autoscaling/ec2/userguide/ec2-auto-scaling-instance-protection.html

- **[참고]ASG 상태 확인 및 교체 메커니즘**
https://docs.aws.amazon.com/ko_kr/autoscaling/ec2/userguide/ec2-auto-scaling-health-checks.html
