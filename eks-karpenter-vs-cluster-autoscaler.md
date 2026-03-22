## 1. 개요

Kubernetes 클러스터 운영에서 리소스 최적화와 비용 효율성은 핵심 과제입니다. 오랫동안 사용되어 온 **Cluster Autoscaler(CA)**는 클라우드 공급자의 ASG(Auto Scaling Group)에 의존하는 한계가 있습니다. 본 문서에서는 **Karpenter**의 'Group-less' 아키텍처가 어떻게 프로비저닝 속도를 높이고 비용을 절감하는지 실무 관점에서 분석합니다.

---

## 2. 설명

### 2.1 아키텍처의 근본적 차이

- **Cluster Autoscaler (Reactive)**: Pending Pod 발생 -> ASG 원하는 용량 수정 -> Cloud Provider가 인스턴스 생성 -> K8s 노드 조인. (ASG라는 중간 레이어 때문에 응답 속도가 느림)
- **Karpenter (Proactive/Direct)**: Pending Pod 발생 -> Pod의 요구사항(CPU, GPU, AZ) 분석 -> **Cloud API 직접 호출** -> 최적의 인스턴스 즉시 생성. (중간 레이어 없음)

### 2.2 실무 적용 코드 (Karpenter NodePool YAML)

CA는 ASG별로 노드 사양을 고정해야 하지만, Karpenter는 `NodePool` 하나로 수백 가지 인스턴스 타입을 유연하게 조합합니다.


```yaml
# karpenter-nodepool.yaml
apiVersion: karpenter.sh/v1beta1
kind: NodePool
metadata:
  name: general-purpose
spec:
  template:
    spec:
      requirements:
        - key: "karpenter.sh/capacity-type"
          operator: In
          values: ["spot", "on-demand"] # 스팟 우선 사용 전략
        - key: "karpenter.k8s.aws/instance-category"
          operator: In
          values: ["c", "m", "r"] # 다양한 인스턴스군 허용
        - key: "kubernetes.io/arch"
          operator: In
          values: ["amd64", "arm64"] # Graviton(비용 절감) 혼합 사용
      nodeClassRef:
        name: default
  # 배포 후 빈 노드나 저효율 노드 자동 정리 (Consolidation)
  disruption:
    consolidationPolicy: WhenUnderutilized
    expireAfter: 720h # 30일 후 노드 교체 (보안 패치 및 수명 관리)
```

### 2.3 보안(Security) 및 비용(Cost) Best Practice

- **보안**: `expireAfter` 설정을 통해 노드의 수명을 강제로 제한함으로써, 장기 실행 노드에서 발생할 수 있는 보안 취약점과 설정 드리프트(Drift)를 방지합니다.
- **비용 (Spot Termination)**: Karpenter는 AWS의 Spot Interruption 알림을 수신하여 노드가 회수되기 전에 워크로드를 우아하게(Graceful) 이동시킵니다.
- **비용 (Right-sizing)**: CA는 정해진 ASG 크기로만 늘리지만, Karpenter는 Pod 크기에 딱 맞는 가장 저렴한 인스턴스를 실시간 경매(Price-capacity optimized) 방식으로 선택합니다.

---

## 3. 트러블슈팅 및 모니터링 전략

### 3.1 주요 이슈: 가용 영역(AZ) 불균형

Karpenter가 비용만 따지다 보면 특정 AZ에 노드가 쏠릴 수 있습니다.

- **해결책**: `topologySpreadConstraints`를 Pod 스펙에 정의하여 Karpenter가 여러 AZ에 노드를 분산 생성하도록 강제해야 합니다.

### 3.2 모니터링 및 알람 전략

Karpenter의 활동은 Prometheus Metric으로 추적합니다.

|**Metric Name**|**Description**|**Alert Threshold**|
|---|---|---|
|`karpenter_nodes_created_counter`|생성된 총 노드 수|급증 시 애플리케이션 무한 루프 의심|
|`karpenter_node_termination_duration_seconds`|노드 삭제 소요 시간|5분 이상 소요 시 배수(Drain) 지연 알람|
|`karpenter_provisions_failed_counter`|프로비저닝 실패 횟수|1회 이상 발생 시 즉시 알람 (Quota 부족 등)|

**Alerting Rule (Prometheus):**

```yaml
groups:
- name: KarpenterAlerts
  rules:
  - alert: KarpenterProvisioningFailed
    expr: increase(karpenter_provisions_failed_counter[5m]) > 0
    for: 1m
    labels:
      severity: critical
    annotations:
      summary: "Karpenter failed to provision nodes"
      description: "Check AWS Service Quotas or Subnet IP availability."
```

---

## 4. 참고자료

- [Karpenter Official Documentation](https://karpenter.sh/)
- [AWS Blog: Leading edge scaling with Karpenter](https://aws.amazon.com/blogs/aws/introducing-karpenter-an-open-source-high-performance-kubernetes-cluster-autoscaler/)
- [Karpenter Best Practices - EKS Workshop](https://www.google.com/search?q=https://www.eksworkshop.com/docs/autoscaling/compute/karpenter/)

---

## 5. TIP

- **Bin-packing**: Karpenter의 핵심은 빈패킹입니다. `consolidationPolicy`를 활성화하면 사용률이 낮은 여러 대의 노드를 한 대의 큰 노드로 합치거나 더 작은 노드로 다운사이징하여 비용을 극적으로 줄여줍니다.    
- **Migration**: CA에서 Karpenter로 한 번에 넘어가기 어렵다면, 특정 `Taint`가 걸린 노드들만 Karpenter가 관리하게 하여 점진적으로 마이그레이션하세요.
- **Limits**: `NodePool`에 `spec.limits`를 설정하여 클러스터 전체 리소스가 예산을 초과하여 무한정 커지는 것을 방지하는 안전장치를 반드시 두세요.
