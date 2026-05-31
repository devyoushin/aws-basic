# 모니터링 작성 규칙 (Monitoring Rules)

이 저장소의 모든 문서에서 모니터링/알람 섹션 작성 시 따라야 할 기준입니다.

---

## 1. CloudWatch 지표 표기 규칙

### 지표 참조 형식
```
{네임스페이스}/{지표명}
```

예시:
- `AWS/EC2/CPUUtilization`
- `AWS/EKS/node_cpu_utilization`
- `AWS/RDS/DatabaseConnections`
- `ContainerInsights/node_memory_utilization`

### 지표 표 필수 컬럼

| 지표 | 네임스페이스 | 단위 | 의미 | 임계값 예시 |
|------|-------------|------|------|------------|
| (지표명) | (네임스페이스) | (단위) | (설명) | (임계값) |

## 2. 알람 설정 작성 규칙

### 기본 알람 템플릿 (CLI)
```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "<SERVICE>-<CONDITION>-<ENV>" \       # 예: ec2-cpu-high-prod
  --alarm-description "<설명>" \
  --namespace "<Namespace>" \
  --metric-name "<MetricName>" \
  --dimensions Name=<DimKey>,Value=<DimValue> \
  --statistic <Average|Sum|Maximum|Minimum|SampleCount> \
  --period <초 단위, 60의 배수> \                    # 300 (5분) 권장
  --evaluation-periods <평가 횟수> \                  # 2~3 권장
  --threshold <임계값> \
  --comparison-operator <GreaterThanThreshold|LessThanThreshold|...> \
  --treat-missing-data <notBreaching|breaching|ignore|missing> \
  --alarm-actions "arn:aws:sns:ap-northeast-2:123456789012:<SNS_TOPIC>" \
  --ok-actions "arn:aws:sns:ap-northeast-2:123456789012:<SNS_TOPIC>" \
  --region ap-northeast-2
```

### 알람 네이밍 규칙
```
{서비스}-{지표요약}-{임계방향}-{환경}
예시:
  ec2-cpu-high-prod
  rds-connections-high-prod
  eks-node-memory-high-staging
```

## 3. 서비스별 필수 지표

### EC2
| 지표 | 임계값 | 이유 |
|------|--------|------|
| `CPUUtilization` | > 80% (5분 평균) | CPU 포화 |
| `StatusCheckFailed_System` | ≥ 1 | 물리 호스트 이상 |
| `StatusCheckFailed_Instance` | ≥ 1 | OS 이상 |
| `EBSWriteBytes` / `EBSReadBytes` | 95% of gp3 limit | I/O 포화 |

### EKS (Container Insights)
| 지표 | 임계값 | 이유 |
|------|--------|------|
| `node_cpu_utilization` | > 80% | 노드 CPU 포화 |
| `node_memory_utilization` | > 80% | 노드 메모리 포화 |
| `pod_cpu_utilization` | > 90% | 파드 CPU throttle |
| `node_filesystem_utilization` | > 85% | 디스크 고갈 |

### RDS / Aurora
| 지표 | 임계값 | 이유 |
|------|--------|------|
| `CPUUtilization` | > 80% | DB CPU 포화 |
| `DatabaseConnections` | > 80% of max_connections | 연결 고갈 |
| `FreeStorageSpace` | < 10GB | 스토리지 부족 |
| `ReplicaLag` | > 30초 | 복제 지연 |

### CloudWatch Logs (Anomaly)
| 패턴 | 심각도 | 설명 |
|------|--------|------|
| `ERROR`, `Exception`, `FATAL` | High | 애플리케이션 오류 |
| `timeout`, `connection refused` | Medium | 네트워크/연결 이슈 |
| `OOMKilled` | High | 메모리 부족 강제 종료 |
| `Throttling`, `RateExceeded` | Medium | API 스로틀링 |

## 4. Composite Alarm 패턴

복수 지표 조합 시 사용:
```bash
aws cloudwatch put-composite-alarm \
  --alarm-name "ec2-unhealthy-prod" \
  --alarm-rule "ALARM(ec2-cpu-high-prod) OR ALARM(ec2-status-check-failed-prod)" \
  --alarm-actions "arn:aws:sns:ap-northeast-2:123456789012:<SNS_TOPIC>"
```

## 5. 모니터링 섹션 품질 기준

새 문서의 모니터링 섹션은 아래를 반드시 충족해야 함:
- [ ] 서비스 관련 지표 **최소 3개** 이상 표에 포함
- [ ] 실제 `put-metric-alarm` CLI 예시 포함
- [ ] 지표 임계값 수치 포함 (막연한 "높을 때" 표현 금지)
- [ ] `treat-missing-data` 값 및 이유 설명 포함
