# 멀티홉 구조 Backend 간헐적 Timeout — UTM 3-way 핸드셰이크 SYN만 관찰

> **실제 원인 확인됨**: 비대칭 라우팅 — SYN-ACK 리턴 경로가 UTM을 우회하여 스테이트풀 세션 테이블 미스 발생

## 증상

- **현상**: 외부에서 Backend 서비스 호출 시 간헐적 TCP Timeout 발생
- **빈도**: 특정 시간대에 집중 (부하 피크 또는 야간 세션 정리 시)
- **관찰**: UTM 패킷 캡처에서 TCP 3-way 핸드셰이크 중 **SYN만 존재**, SYN-ACK 없음
- **서비스**: Istio Sidecar가 주입된 EKS Pod에서 실행되는 Backend API

---

## 아키텍처

```
외부 서비스
    │
    ▼
[NLB] ← 랜딩존 (Landing Zone Account)
    │
    ▼
[UTM] ← 스테이트풀 방화벽 / 패킷 캡처 지점
    │
    ▼
[NLB]
    │
    ▼
[WAF]
    │
    ▼
[TGW] ← 계정 경계 (서비스 Account로 진입)
    │
    ▼
[ALB]
    │
    ▼
[API Gateway (Private)]
    │
    ▼
[NLB]
    │
    ▼
[EKS Pod] ← Istio Envoy Sidecar 주입됨
```

> **핵심**: NLB는 Layer 4로 동작하며 Source IP를 보존합니다.
> TGW를 경유하면 리턴 경로가 다를 수 있어 **비대칭 라우팅**이 발생할 수 있습니다.

---

## 원인 분석

### ✔ 1순위 — **실제 원인**: 비대칭 라우팅 (Asymmetric Routing)

UTM은 **스테이트풀(Stateful) 방화벽**으로 동작합니다.

```
[정상 흐름]
외부 → UTM (SYN 기록) → NLB → ... → EKS
외부 ← UTM (SYN-ACK 확인) ← NLB ← ... ← EKS

[비대칭 흐름 — 문제 상황]
외부 → UTM (SYN 기록) → NLB → ... → EKS
외부 ← (다른 경로로 SYN-ACK 복귀, UTM 미통과)
         → UTM이 세션 테이블에서 SYN-ACK를 못 봄
         → 이후 ACK/데이터 패킷을 "연결 없는 패킷"으로 드롭
```

**발생 조건:**
- TGW 라우팅 테이블에 복수의 경로가 존재할 때
- NLB가 다수의 AZ에 걸쳐 있고, 요청/응답이 서로 다른 AZ NLB 노드를 통과할 때
- UTM이 Active-Active HA 구성이고 세션 동기화 지연이 있을 때

### 2순위 가설 (해당 없음): UTM 세션 테이블 고갈

특정 시간(피크 트래픽, 야간 배치)에 UTM의 concurrent session 한도 도달:

```
UTM Session Table Full
→ 신규 SYN은 수신했으나 세션 항목 생성 불가
→ SYN-ACK를 전달하지 못함 (또는 응답 경로에서 드롭)
```

### 3순위 가설 (해당 없음): Istio Sidecar 과부하 / 타임아웃 미스매치

```
TCP 3-way 핸드셰이크는 성공 (SYN-ACK 정상)
↓
Istio Envoy가 HTTP 요청을 처리하다 upstream timeout 초과
↓
TCP RST 또는 응답 없음
↓
클라이언트 쪽에서는 연결 자체가 안 된 것처럼 보임
↓
UTM 로그에서는 완성되지 않은 세션으로 SYN만 기록됨
```

**Istio 타임아웃 기본값 주의:**
- `VirtualService.spec.http.timeout` 기본값: 15s
- Envoy upstream idle timeout: 1h (but connection pool 재사용 문제 발생 가능)
- API Gateway 하드 타임아웃: **29초** (조정 불가)

### 4순위 가설 (해당 없음): NLB 유휴 연결 타임아웃

NLB의 기본 TCP idle timeout은 **350초**이며, 이보다 짧은 커넥션 풀을 사용하는 클라이언트는 half-open 상태의 연결에 데이터를 전송하다 RST를 받을 수 있습니다.

---

## 진단 절차

### Step 1: 비대칭 라우팅 여부 확인

**VPC Flow Logs로 리턴 경로 추적:**

```bash
# 랜딩존 VPC — UTM ENI에서 SYN-ACK 수신 여부 확인
aws logs start-query \
  --log-group-name "/vpc/landing-zone-flow-logs" \
  --start-time $(date -d '1 hour ago' +%s) \
  --end-time $(date +%s) \
  --query-string '
    fields @timestamp, srcAddr, dstAddr, srcPort, dstPort, action, tcpFlags
    | filter tcpFlags = 2          # SYN = 0x02
    | filter dstAddr like /UTM_IP/
    | sort @timestamp desc
    | limit 100
  '

# SYN-ACK (tcpFlags = 18 = 0x12) 가 UTM ENI를 통과하는지 확인
aws logs start-query \
  --log-group-name "/vpc/landing-zone-flow-logs" \
  --start-time $(date -d '1 hour ago' +%s) \
  --end-time $(date +%s) \
  --query-string '
    fields @timestamp, srcAddr, dstAddr, srcPort, dstPort, tcpFlags
    | filter tcpFlags = 18         # SYN-ACK = 0x12
    | filter srcAddr like /BACKEND_CIDR/
    | sort @timestamp desc
    | limit 100
  '
```

**리턴 경로가 UTM을 우회하는지 확인:**
```bash
# TGW Flow Logs — 동일 5-tuple의 양방향 트래픽 대조
aws logs start-query \
  --log-group-name "/tgw/flow-logs" \
  --query-string '
    fields @timestamp, srcAddr, dstAddr, srcPort, dstPort, tcpFlags, interfaceId
    | filter srcPort = CLIENT_PORT or dstPort = CLIENT_PORT
    | sort @timestamp asc
  '
```

### Step 2: UTM 세션 테이블 포화 확인

UTM 관리 콘솔 또는 SNMP에서 아래 지표 확인:

| 지표 | 정상 | 경고 |
|------|------|------|
| Current Sessions | < 80% capacity | ≥ 80% |
| Session Creation Rate | 안정적 | 급등 후 급락 |
| Half-open Sessions (SYN_SENT) | < 1,000 | 급증 |

```bash
# CloudWatch — UTM이 EC2 기반이라면 네트워크 지표 확인
aws cloudwatch get-metric-statistics \
  --namespace AWS/EC2 \
  --metric-name NetworkPacketsIn \
  --dimensions Name=InstanceId,Value=i-UTM_INSTANCE_ID \
  --start-time 2026-05-06T00:00:00Z \
  --end-time 2026-05-06T23:59:59Z \
  --period 60 \
  --statistics Sum
```

### Step 3: Istio 타임아웃 / 연결 상태 확인

```bash
# Envoy 통계 — upstream 연결 실패 / 타임아웃 확인
kubectl exec -n <namespace> <pod-name> -c istio-proxy -- \
  pilot-agent request GET stats | grep -E "upstream_cx_connect_timeout|upstream_rq_timeout|upstream_rq_pending_overflow"

# Istio 프록시 로그에서 upstream timeout 확인
kubectl logs -n <namespace> <pod-name> -c istio-proxy \
  | grep -E "upstream request timeout|reset reason|UF|UC|UT"
```

**Envoy 리셋 코드 해석:**

| 코드 | 의미 |
|------|------|
| `UF` | Upstream connection failure |
| `UC` | Upstream connection termination |
| `UT` | Upstream request timeout |
| `URX` | Upstream retry limit exceeded |
| `NR` | No route found |

```bash
# VirtualService 타임아웃 확인
kubectl get virtualservice -A -o yaml | grep -A3 timeout

# DestinationRule 연결 풀 설정 확인
kubectl get destinationrule -A -o yaml | grep -A10 connectionPool
```

### Step 4: NLB 헬스체크 / 연결 상태 확인

```bash
# 타임아웃 발생 시점 NLB 타겟 헬스 히스토리
aws elbv2 describe-target-health \
  --target-group-arn arn:aws:elasticloadbalancing:ap-northeast-2:ACCOUNT:targetgroup/TG_NAME/ID

# NLB Access Log (S3 활성화 필요) 에서 처리 지연 확인
aws s3 cp s3://nlb-access-log-bucket/PREFIX/ . --recursive
# 로그 필드: time type elb client:port target:port request_processing_time ...
```

### Step 5: API Gateway 타임아웃 확인

```bash
# API Gateway 통합 타임아웃 설정 확인 (최대 29초)
aws apigateway get-integration \
  --rest-api-id API_ID \
  --resource-id RESOURCE_ID \
  --http-method POST \
  | jq '.timeoutInMillis'

# API Gateway CloudWatch 지표 — IntegrationLatency 확인
aws cloudwatch get-metric-statistics \
  --namespace AWS/ApiGateway \
  --metric-name IntegrationLatency \
  --dimensions Name=ApiName,Value=API_NAME \
  --start-time 2026-05-06T00:00:00Z \
  --end-time 2026-05-06T23:59:59Z \
  --period 60 \
  --statistics p99 Maximum
```

---

## 해결 방법

### 비대칭 라우팅이 원인인 경우

**TGW 라우팅 테이블 정리 — 리턴 경로 고정:**

```hcl
# Terraform — TGW 라우팅 테이블에서 특정 CIDR의 경로 단일화
resource "aws_ec2_transit_gateway_route" "return_path" {
  destination_cidr_block         = "10.0.0.0/8"  # 랜딩존 CIDR
  transit_gateway_attachment_id  = aws_ec2_transit_gateway_vpc_attachment.landing_zone.id
  transit_gateway_route_table_id = aws_ec2_transit_gateway_route_table.service.id
}
```

**NLB — Cross-Zone Load Balancing 비활성화 (비대칭 라우팅 방지):**

```bash
aws elbv2 modify-load-balancer-attributes \
  --load-balancer-arn arn:aws:elasticloadbalancing:ap-northeast-2:ACCOUNT:loadbalancer/net/NLB_NAME/ID \
  --attributes Key=load_balancing.cross_zone.enabled,Value=false
```

> **주의**: Cross-Zone 비활성화 시 AZ 간 부하 분산이 불균등해질 수 있음. AZ별 타겟 수를 균등하게 유지해야 함.

**UTM HA — 세션 동기화 설정 확인:**
- Active-Active 구성 시 세션 동기화(Session Sync) 활성화 여부 확인
- Active-Passive로 전환 검토 (세션 동기화 지연 제거)

### Istio 타임아웃 미스매치가 원인인 경우

**VirtualService 타임아웃 정렬 — API Gateway(29s) 기준으로 설정:**

```yaml
apiVersion: networking.istio.io/v1beta1
kind: VirtualService
metadata:
  name: backend-service
  namespace: backend
spec:
  hosts:
    - backend-service
  http:
    - timeout: 25s          # API Gateway 29s보다 짧게 설정
      retries:
        attempts: 2
        perTryTimeout: 10s
        retryOn: gateway-error,connect-failure,retriable-4xx
      route:
        - destination:
            host: backend-service
            port:
              number: 8080
```

**DestinationRule — 연결 풀 및 이상 감지 설정:**

```yaml
apiVersion: networking.istio.io/v1beta1
kind: DestinationRule
metadata:
  name: backend-service
  namespace: backend
spec:
  host: backend-service
  trafficPolicy:
    connectionPool:
      tcp:
        maxConnections: 1000
        connectTimeout: 5s
        tcpKeepalive:
          time: 300s          # NLB idle timeout(350s)보다 짧게
          interval: 60s
          probes: 3
      http:
        h2UpgradePolicy: NEVER   # HTTP/1.1 강제 (API Gateway 호환성)
        idleTimeout: 295s        # NLB 350s보다 짧게
    outlierDetection:
      consecutive5xxErrors: 3
      interval: 10s
      baseEjectionTime: 30s
```

### NLB Idle Timeout이 원인인 경우

```bash
# NLB TCP idle timeout 조정 (기본 350s → 애플리케이션 keep-alive와 맞춤)
aws elbv2 modify-load-balancer-attributes \
  --load-balancer-arn arn:aws:elasticloadbalancing:ap-northeast-2:ACCOUNT:loadbalancer/net/NLB_NAME/ID \
  --attributes Key=idle_timeout.timeout_seconds,Value=300
```

---

## 재발 방지

### CloudWatch 알람 — 구간별 이상 감지

```bash
# NLB — UnHealthyHostCount 알람
aws cloudwatch put-metric-alarm \
  --alarm-name "nlb-unhealthy-host-backend" \
  --namespace AWS/NetworkELB \
  --metric-name UnHealthyHostCount \
  --dimensions Name=LoadBalancer,Value=net/NLB_NAME/ID \
  --statistic Maximum \
  --period 60 \
  --threshold 1 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --evaluation-periods 2 \
  --alarm-actions arn:aws:sns:ap-northeast-2:ACCOUNT:alert-topic

# API Gateway — p99 IntegrationLatency 알람
aws cloudwatch put-metric-alarm \
  --alarm-name "apigw-integration-latency-p99" \
  --namespace AWS/ApiGateway \
  --metric-name IntegrationLatency \
  --dimensions Name=ApiName,Value=API_NAME \
  --extended-statistic p99 \
  --period 60 \
  --threshold 20000 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 3 \
  --alarm-actions arn:aws:sns:ap-northeast-2:ACCOUNT:alert-topic
```

### Istio 메트릭 기반 알람 (Prometheus / CloudWatch EMF)

```yaml
# Prometheus 알람 규칙 예시
groups:
  - name: istio-timeout
    rules:
      - alert: IstioUpstreamTimeout
        expr: |
          rate(istio_requests_total{
            response_flags=~"UT|UF|UC",
            destination_service=~"backend-service.*"
          }[5m]) > 0.01
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "Istio upstream timeout/failure rate 초과"
          description: "{{ $labels.destination_service }} 응답 플래그: {{ $labels.response_flags }}"
```

### VPC Flow Logs 상시 수집 체계

```hcl
# Terraform — 랜딩존 VPC Flow Logs (UTM ENI 포함)
resource "aws_flow_log" "landing_zone" {
  vpc_id          = aws_vpc.landing_zone.id
  traffic_type    = "ALL"
  iam_role_arn    = aws_iam_role.flow_log.arn
  log_destination = aws_cloudwatch_log_group.flow_log.arn

  # 확장 포맷 — TCP 플래그 포함
  log_format = "$${version} $${account-id} $${interface-id} $${srcaddr} $${dstaddr} $${srcport} $${dstport} $${protocol} $${packets} $${bytes} $${start} $${end} $${action} $${log-status} $${tcp-flags} $${pkt-srcaddr} $${pkt-dstaddr}"
}
```

### 구간별 헬스체크 대시보드

| 구간 | 지표 | 임계값 |
|------|------|--------|
| UTM → NLB | VPC Flow Logs SYN 미완성 세션 수 | > 10/min |
| NLB 헬스체크 | UnHealthyHostCount | ≥ 1 |
| TGW | BytesDropCount | > 0 |
| ALB | TargetResponseTime p99 | > 5s |
| API Gateway | IntegrationLatency p99 | > 20s |
| Istio | response_flags `UT/UF/UC` 비율 | > 1% |
| EKS Pod | container_memory_working_set_bytes | > requests * 0.9 |

---

## 체크리스트 (재발생 시 즉시 확인)

```
[ ] 1. UTM 패킷 캡처 — SYN-ACK 응답 여부 및 출구 인터페이스 확인
[ ] 2. VPC Flow Logs — 리턴 경로(SYN-ACK)가 UTM을 경유하는지 확인
[ ] 3. TGW 라우팅 테이블 — 동일 CIDR에 대한 경로 중복 여부 확인
[ ] 4. UTM 세션 테이블 사용률 확인 (capacity 대비 %)
[ ] 5. NLB 타겟 헬스체크 상태 확인
[ ] 6. Istio Envoy stats — UT/UF/UC 플래그 발생 여부
[ ] 7. API Gateway IntegrationLatency p99 확인 (29s 근접 여부)
[ ] 8. EKS 노드/Pod 리소스 포화 여부 (CPU throttle, OOMKilled)
```

---

## 관련 문서

- [`docs/network/vpc-flow-logs-path-monitoring.md`](../network/vpc-flow-logs-path-monitoring.md) — 구간별 Flow Logs 모니터링
- [`docs/network/aws-transit-gateway.md`](../network/aws-transit-gateway.md) — TGW 라우팅 설계
- [`docs/network/nlb-ec2-port-forwarding.md`](../network/nlb-ec2-port-forwarding.md) — NLB 헬스체크 트러블슈팅
- [`docs/eks/eks-networking-vpc-cni.md`](../eks/eks-networking-vpc-cni.md) — EKS 네트워크 구조
- [`docs/security/waf-rate-limiting.md`](../security/waf-rate-limiting.md) — WAF False Positive 대응
