# AWS Enterprise Landing Zone 아키텍처

## 1. 개요

AWS 엔터프라이즈 환경에서 Landing Zone은 멀티 계정 구조의 기반이 되는 표준 설계다.
Organizations OU 계층으로 보안 경계를 분리하고, SCP 가드레일로 전체 계정에 정책을 일관 적용한다.
모니터링은 별도 Monitoring Account에 OAM Sink를 두어 계정별 로그인 없이 중앙 관찰한다.

---

## 2. 전체 구성도

```mermaid
graph TD
    ROOT["🏢 Root (Management Account)\n Organizations 관리 · 통합 결제"]

    ROOT --> SEC_OU["🔒 Security OU"]
    ROOT --> INFRA_OU["⚙️ Infrastructure OU"]
    ROOT --> WORK_OU["💼 Workloads OU"]
    ROOT --> SAND_OU["🧪 Sandbox OU"]

    SEC_OU --> LOG["📦 Log Archive Account\n CloudTrail · VPC Flow Logs\n S3 Object Lock (불변)"]
    SEC_OU --> SECTOOL["🛡️ Security Tooling Account\n GuardDuty Master\n Security Hub Aggregator\n AWS Config Aggregator"]

    INFRA_OU --> NET["🌐 Network Account\n Transit Gateway · DX\n Route53 Resolver · VPN"]
    INFRA_OU --> SHARED["🔧 Shared Services Account\n ECR · Artifactory\n 내부 도구"]
    INFRA_OU --> MON["📊 Monitoring Account\n OAM Sink\n 통합 Dashboard · Alarm\n X-Ray Traces"]

    WORK_OU --> PROD_OU["🚀 Production OU\n SCP: 엄격한 제한"]
    WORK_OU --> NONPROD_OU["🔨 NonProd OU\n SCP: 중간 제한"]

    PROD_OU --> PROD_A["Prod App-A Account"]
    PROD_OU --> PROD_B["Prod App-B Account"]
    PROD_OU --> PROD_C["Prod App-C Account"]

    NONPROD_OU --> STG["Staging Account"]
    NONPROD_OU --> DEV["Dev Account"]

    SAND_OU --> SAND["🏖️ Sandbox Account\n 월 비용 상한\n 인스턴스 타입 제한"]

    PROD_A -->|OAM Link| MON
    PROD_B -->|OAM Link| MON
    PROD_C -->|OAM Link| MON
    STG -->|OAM Link| MON
    DEV -->|OAM Link| MON

    PROD_A -->|CloudTrail| LOG
    PROD_B -->|CloudTrail| LOG
    STG -->|CloudTrail| LOG

    PROD_A -->|GuardDuty 멤버| SECTOOL
    PROD_B -->|GuardDuty 멤버| SECTOOL

    NET -->|TGW Attachment| PROD_A
    NET -->|TGW Attachment| PROD_B
    NET -->|TGW Attachment| MON

    style ROOT fill:#232F3E,color:#fff
    style SEC_OU fill:#DD344C,color:#fff
    style INFRA_OU fill:#E47911,color:#fff
    style WORK_OU fill:#1A73E8,color:#fff
    style SAND_OU fill:#34A853,color:#fff
    style PROD_OU fill:#1557A0,color:#fff
    style NONPROD_OU fill:#1a5276,color:#fff
    style MON fill:#7B2FBE,color:#fff
    style LOG fill:#8B0000,color:#fff
    style SECTOOL fill:#8B0000,color:#fff
```

---

## 3. 데이터 흐름

```mermaid
flowchart LR
    WL["워크로드 계정\nProd / Dev"]

    WL -->|"Org Trail"| LOG["📦 Log Archive\nS3 불변 저장"]
    WL -->|"멤버 자동 등록"| SEC["🛡️ Security Tooling\nGuardDuty · Security Hub"]
    WL -->|"OAM Link"| MON["📊 Monitoring Account\n지표 · 로그 · 트레이스"]
    WL -->|"TGW Attachment"| NET["🌐 Network Account\n온프레미스 · 계정 간 통신"]
```

---

## 4. OU별 계정 역할 및 SCP 강도

| OU | 계정 | 역할 | SCP 제한 수준 | 주요 가드레일 |
|----|------|------|--------------|--------------|
| Root | Management | Organizations 관리, 통합 결제 | 공통 기본 | CloudTrail 비활성화 금지, Root 사용 금지, 리전 제한 |
| Security | Log Archive | CloudTrail/Flow Logs 중앙 저장 | 최고 | S3 삭제·수정 금지 (Object Lock + SCP) |
| Security | Security Tooling | GuardDuty/Security Hub/Config 집계 | 최고 | 보안팀만 접근, 설정 변경 감지 알람 |
| Infrastructure | Network | TGW, DX, Route53 Resolver, VPN | 높음 | 네트워크팀만 변경 가능 |
| Infrastructure | Shared Services | ECR, 내부 도구 공유 | 높음 | 퍼블릭 공개 금지 |
| Infrastructure | Monitoring | OAM Sink, 통합 대시보드/알람 | 높음 | 읽기 전용 접근 분리 |
| Workloads | Production | 실제 서비스 운영 | 높음 | IMDSv2 강제, 퍼블릭 S3 ACL 금지 |
| Workloads | NonProd | 개발·스테이징 환경 | 중간 | 일부 인스턴스 타입 제한 |
| Sandbox | Sandbox | 개발자 실험 | 낮음 + 비용 상한 | 고비용 인스턴스 금지, Budget 초과 시 배포 차단 |

---

## 5. 모니터링 알람 계층 구조

```mermaid
graph TD
    R1["리소스 알람 (워크로드 계정)\nCPU > 80%\nEBS BurstBalance < 20%\n5xx Error Rate > 1%"]
    R2["Composite Alarm (워크로드 계정)\nCPU 과부하 AND 네트워크 급증\n→ 앱서버 과부하"]
    R3["크로스 계정 통합 알람 (Monitoring Account)\n전체 서비스 건강도\nSLA 위반 감지"]

    R1 -->|AND/OR 조합| R2
    R2 -->|OAM Link| R3

    R3 --> P1["🔴 P1/P2\nPagerDuty / OpsGenie\n즉시 온콜"]
    R3 --> P2["🟡 P3\nSlack #alerts\n업무시간 대응"]
    R3 --> P3["🟢 P4\nJIRA 자동 티켓\n다음날 처리"]

    style R1 fill:#1a5276,color:#fff
    style R2 fill:#1557A0,color:#fff
    style R3 fill:#7B2FBE,color:#fff
    style P1 fill:#DD344C,color:#fff
    style P2 fill:#E47911,color:#fff
    style P3 fill:#34A853,color:#fff
```

---

## 6. 실무 적용 포인트

- **Control Tower 활용**: 계정 팩토리(Account Factory)로 신규 계정 생성 시 Log Archive 연결, GuardDuty 활성화, OAM Link 생성 자동화
- **IP 주소 계획**: 멀티 계정 환경에서 계정별 VPC CIDR 충돌 시 TGW 연결 불가 — 계정별 `/16` 블록을 사전 할당 (`vpc-subnet-design.md` 참고)
- **새 계정 Day 1 베이스라인**: Account Factory Customization(CfCT)으로 계정 생성 즉시 CWAgent, GuardDuty, 기본 알람 자동 배포
- **Runbook 자동 연결**: 알람 → SNS → Lambda → SSM Runbook 자동 실행 (재시작, 스냅샷 등)

---

## 7. 관련 문서

- [`aws-organizations-multi-account.md`](../cost/aws-organizations-multi-account.md) — OU 구조, SCP, IAM Identity Center 상세
- [`cloudwatch-cross-account.md`](../cloudwatch/cloudwatch-cross-account.md) — OAM Sink/Link 설정 코드
- [`cloudwatch-alarm-composite.md`](../cloudwatch/cloudwatch-alarm-composite.md) — Composite Alarm 설계
- [`vpc-subnet-design.md`](../network/vpc-subnet-design.md) — 멀티 계정 CIDR 설계
- [`cloudtrail-security-audit.md`](../security/cloudtrail-security-audit.md) — Org Trail 구성
