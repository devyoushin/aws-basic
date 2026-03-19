## 1. 개요

Kubernetes의 `imagePullPolicy`는 노드가 컨테이너를 시작할 때 이미지를 레지스트리(ECR 등)에서 새로 다운로드할지, 로컬 캐시를 사용할지 결정하는 설정입니다. 잘못된 설정은 **배포 지연, 대역폭 비용 상승, 또는 최신 코드 미반영** 등의 문제를 야기할 수 있으므로 명확한 전략이 필요합니다.

## 2. 설명

### Image Pull Policy의 3가지 유형

1. **`Always`**: 컨테이너가 시작될 때마다 항상 레지스트리에서 이미지 Digest를 확인하고 변경사항이 있다면 다운로드합니다. 태그가 `:latest`인 경우 기본값입니다.
2. **`IfNotPresent`**: 노드 로컬에 이미지가 없을 때만 다운로드합니다. 태그가 특정 버전(v1.0.0 등)일 때의 기본값입니다.
3. **`Never`**: 로컬에 이미지가 있다고 가정하며, 레지스트리 체크를 하지 않습니다. 이미지가 없으면 `ErrImageNeverPull` 에러와 함께 실패합니다.
    

### 실무 적용 코드 (Helm & YAML)

#### [Helm Chart - values.yaml]
실무에서는 환경별로 정책을 다르게 가져갑니다. 운영 환경에서는 가급적 **Immutable Tag(버전 고정)**를 사용하고 `IfNotPresent`를 사용하는 것이 효율적입니다.

```yaml
# values.yaml 예시
image:
  repository: <aws_account_id>.dkr.ecr.ap-northeast-2.amazonaws.com/my-app
  tag: "v1.2.3" # SHA나 Build ID 권장
  pullPolicy: IfNotPresent

# CI/CD 단계에서 특정 브랜치(Dev)인 경우 Always로 오버라이드 가능
```

#### [Terraform - ECR Access Policy]
EKS 노드가 이미지를 당겨오기 위해서는 IAM 권한이 필수입니다.

```Terraform
resource "aws_iam_role_policy_attachment" "eks_node_ecr_read" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
  role       = aws_iam_role.eks_node_role.name
}
```

## 3. 트러블슈팅

### Case 1: ImagePullBackOff / ErrImagePull

- **증상**: Pod가 생성되지 않고 `ImagePullBackOff` 상태에 머무름.
- **원인**:
    
    1. ECR 프라이빗 레지스트리 인증 실패 (IAM Role 권한 부족).
    2. NAT Gateway 또는 ECR VPC Endpoint 부재로 인한 네트워크 단절.
    3. 이미지 태그 오타.
        
- **해결**: `kubectl describe pod [POD_NAME]` 명령어로 `Events` 섹션을 확인하여 상세 에러 메시지(403 Forbidden, Timeout 등)를 파악합니다.
    

### Case 2: 코드는 수정했는데 예전 이미지가 실행됨

- **증상**: 이미지를 푸시했으나 Pod 재시작 후에도 이전 소스코드가 보임.
- **원인**: 동일한 태그(예: `latest`, `develop`)를 사용하면서 `IfNotPresent`를 설정한 경우. 노드는 이미 해당 이름의 태그가 로컬에 있으므로 레지스트리를 확인하지 않습니다.
- **해결**: 정책을 `Always`로 변경하거나, 배포 시마다 고유한 태그(Git Commit Hash 등)를 사용하도록 CI/CD 파이프라인을 수정합니다.

## 4. 참고자료

- [Kubernetes Documentation - Images](https://www.google.com/search?q=https://kubernetes.io/docs/concepts/containers/images/)
- [AWS Blog - Optimized ECR Image Pulling](https://www.google.com/search?q=https://aws.amazon.com/blogs/containers/speeding-up-container-image-pulls-on-eks/)
    

## TIP (Monitoring & Security)

### 모니터링 및 알람 (Alerting)
`kube-state-metrics`를 사용하여 `ImagePullBackOff` 발생 시 Slack/PagerDuty 알람을 설정해야 합니다.


```yaml
# PrometheusRule 예시
- alert: EKSImagePullFailed
  expr: kube_pod_container_status_waiting_reason{reason="ImagePullBackOff"} > 0
  for: 5m
  labels:
    severity: critical
  annotations:
    summary: "이미지 풀링 실패: {{ $labels.pod }}"
```

### 보안 및 비용 Best Practice

1. **Cost**: `Always` 정책을 남발하면 노드 스케일링 시마다 데이터 전송 비용(Data Transfer Out)이 발생합니다. 동일 VPC 내라면 **ECR Interface VPC Endpoint**를 생성하여 비용을 절감하고 내부망 통신을 강제하세요.
2. **Security**: 이미지 태그를 `latest`로 두면 배포된 시점의 이미지가 무엇인지 파악하기 어렵습니다. 보안 취약점 스캔(Amazon Inspector) 결과와 매칭하기 위해 반드시 **Immutable Tag**를 사용하세요.
3. **Efficiency**: 대용량 이미지의 경우 `zstd` 압축을 사용하거나, EKS에서 지원하는 **Seekable OCI (SOCI)**를 활용해 지연 로딩(Lazy Loading)을 적용하면 시작 시간을 단축할 수 있습니다.
