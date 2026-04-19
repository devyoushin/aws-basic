# EKS Pod 보안 — Pod Security Admission & SecurityContext

## 1. 개요

EKS에서 Pod 보안은 두 레이어로 구성된다.
- **Pod Security Admission (PSA)**: Kubernetes 내장 기능, namespace 단위로 보안 표준 적용
- **SecurityContext**: 개별 Pod/Container의 런타임 권한 제어

과거의 Pod Security Policy (PSP)는 Kubernetes 1.25에서 제거되었으며, PSA가 공식 대체 방안이다.

---

## 2. 설명

### 2.1 핵심 개념

**Pod Security Standards 3단계**

| 레벨 | 설명 | 주요 제한 |
|------|------|---------|
| `privileged` | 제한 없음 | 없음 (시스템 컴포넌트용) |
| `baseline` | 기본 보안 | 특권 컨테이너, hostNetwork/PID 금지 |
| `restricted` | 강화된 보안 | runAsNonRoot 필수, 모든 capabilities 삭제 |

**PSA 모드**

| 모드 | 동작 |
|------|------|
| `enforce` | 정책 위반 Pod 거부 |
| `audit` | 위반 허용, 감사 로그에 기록 |
| `warn` | 위반 허용, API 응답에 경고 |

---

### 2.2 실무 적용 코드

**Namespace에 PSA Label 설정**

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: production
  labels:
    # enforce: 위반 시 Pod 생성 거부
    pod-security.kubernetes.io/enforce: restricted
    # audit: 감사 로그 기록
    pod-security.kubernetes.io/audit: restricted
    # warn: API 응답에 경고
    pod-security.kubernetes.io/warn: restricted
    # 버전 고정 (Kubernetes 버전 업그레이드 후 갑작스러운 정책 강화 방지)
    pod-security.kubernetes.io/enforce-version: v1.29
```

```bash
# 기존 namespace에 PSA 적용
kubectl label namespace production \
  pod-security.kubernetes.io/enforce=baseline \
  pod-security.kubernetes.io/warn=restricted

# namespace의 기존 Pod가 정책을 위반하는지 사전 확인
kubectl label namespace production \
  pod-security.kubernetes.io/enforce=restricted \
  --dry-run=server
```

**SecurityContext — restricted 정책을 만족하는 Deployment**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-app
  namespace: production
spec:
  template:
    spec:
      # Pod 레벨 SecurityContext
      securityContext:
        runAsNonRoot: true            # root로 실행 금지
        runAsUser: 1000               # 실행 UID
        runAsGroup: 1000              # 실행 GID
        fsGroup: 1000                 # 파일시스템 그룹 (볼륨 접근용)
        seccompProfile:
          type: RuntimeDefault        # 기본 seccomp 프로파일 (restricted 필수)
      containers:
        - name: app
          image: my-app:latest
          # Container 레벨 SecurityContext
          securityContext:
            allowPrivilegeEscalation: false   # setuid/setgid 실행 금지
            readOnlyRootFilesystem: true       # 루트 파일시스템 읽기 전용
            runAsNonRoot: true
            runAsUser: 1000
            capabilities:
              drop:
                - ALL                # 모든 Linux capabilities 제거 (restricted 필수)
              # add:                 # 필요한 경우에만 추가
              #   - NET_BIND_SERVICE  # 1024 미만 포트 바인딩 시 필요
          # readOnlyRootFilesystem 사용 시 쓰기 필요한 경로는 emptyDir로
          volumeMounts:
            - name: tmp
              mountPath: /tmp
            - name: app-cache
              mountPath: /app/cache
      volumes:
        - name: tmp
          emptyDir: {}
        - name: app-cache
          emptyDir: {}
```

**Falco — 런타임 보안 감지**

```bash
# Falco 설치 (eBPF 기반 런타임 위협 탐지)
helm repo add falcosecurity https://falcosecurity.github.io/charts
helm install falco falcosecurity/falco \
  --namespace falco \
  --create-namespace \
  --set driver.kind=ebpf \
  --set falcosidekick.enabled=true \
  --set falcosidekick.config.slack.webhookurl="https://hooks.slack.com/xxx"
```

```yaml
# 커스텀 Falco 규칙 예시
customRules:
  custom-rules.yaml: |-
    - rule: Shell in Production Container
      desc: 프로덕션 컨테이너에서 shell 실행 감지
      condition: >
        spawned_process and
        container and
        container.image.repository contains "my-app" and
        proc.name in (bash, sh, zsh)
      output: >
        프로덕션 컨테이너에서 shell 실행
        (user=%user.name cmd=%proc.cmdline container=%container.name)
      priority: WARNING
```

---

### 2.3 보안/비용 Best Practice

- **단계적 PSA 적용**: `warn` → `audit` → `enforce` 순서로 단계적 적용
- **시스템 네임스페이스 제외**: `kube-system` 등에는 PSA를 적용하지 않거나 `privileged`로 설정
- **컨테이너 이미지를 non-root로 빌드**:
  ```dockerfile
  FROM node:18-alpine
  RUN addgroup -S appgroup && adduser -S appuser -G appgroup
  USER appuser   # non-root 사용자로 전환
  ```
- **readOnlyRootFilesystem**: 컨테이너 파일시스템 변조 방지 (악성코드 쓰기 차단)

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**restricted 정책에서 Pod 생성 실패**

```bash
# 오류 메시지 예시
# Error: pods "my-app-xxx" is forbidden:
# violates PodSecurity "restricted:latest":
# allowPrivilegeEscalation != false, ...

# 어떤 정책을 위반했는지 확인
kubectl describe pod my-app-xxx -n production | grep -A 5 "Events:"

# 정책 위반 없이 배포 가능한지 dry-run
kubectl apply -f deployment.yaml --dry-run=server
```

**readOnlyRootFilesystem으로 인한 애플리케이션 오류**

```bash
# 오류: "Read-only file system" 또는 "Permission denied"

# 어떤 경로에 쓰기를 시도하는지 확인 (strace 또는 로그)
kubectl logs my-app-pod -n production | grep -i "read-only\|permission"

# 해결: 쓰기 필요한 경로를 emptyDir로 마운트
# /tmp, /app/logs, /app/cache 등
```

**runAsNonRoot 위반 — 이미지가 root로 실행**

```bash
# 이미지의 기본 USER 확인
docker inspect my-app:latest | jq '.[0].Config.User'
# "" (빈 문자열) = root

# Dockerfile에 USER 추가 또는
# SecurityContext에 runAsUser 명시
securityContext:
  runAsUser: 65534   # nobody 사용자
  runAsNonRoot: true
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: kube-system의 DaemonSet에 PSA를 적용하면 문제가 생기나요?**
A: `kube-system`의 컴포넌트(aws-node, kube-proxy 등)는 privileged 권한이 필요하므로 `restricted` 정책을 적용하면 배포 불가합니다. `kube-system`은 PSA에서 제외하거나 `privileged` 레벨로 설정하세요.

**Q: NET_BIND_SERVICE capability는 언제 필요한가요?**
A: 80, 443 같은 1024 미만 포트에 바인딩할 때 필요합니다. 대신 포트를 8080 등 1024 이상으로 변경하고 Service에서 포트 변환하는 방법을 권장합니다.

---

## 4. 모니터링 및 알람

```bash
# PSA 위반 감사 로그 확인 (audit 모드에서)
kubectl get events -A | grep "FailedCreate\|PodSecurity"

# Falco 이벤트 확인
kubectl logs -n falco daemonset/falco | grep "WARNING\|ERROR\|CRITICAL"
```

```hcl
# CloudWatch — Falco CRITICAL 이벤트 알람
resource "aws_cloudwatch_log_metric_filter" "falco_critical" {
  name           = "falco-critical-events"
  pattern        = "\"CRITICAL\""
  log_group_name = "/eks/falco"

  metric_transformation {
    name      = "FalcoCriticalEvents"
    namespace = "Custom/EKSSecurity"
    value     = "1"
  }
}
```

---

## 5. TIP

- **PSP → PSA 마이그레이션**: Kubernetes 1.25에서 PSP 제거됨. `kubectl-convert`와 [PSP → PSA 마이그레이션 가이드](https://kubernetes.io/docs/tasks/configure-pod-container/migrate-from-psp/) 참고
- **OPA Gatekeeper / Kyverno**: PSA보다 세밀한 정책이 필요하면 (특정 이미지 레지스트리만 허용, 리소스 requests 필수화 등) 정책 엔진 도입 고려
- **Amazon Inspector**: EKS 워크로드의 컨테이너 이미지 취약점 스캔 및 ECR 이미지 스캔과 통합
