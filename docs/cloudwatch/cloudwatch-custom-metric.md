## 1. 개요
EC2의 CPU나 디스크 I/O 같은 기본 지표(Default Metrics) 외에, **메모리(RAM) 사용량, 디스크 남은 공간, 애플리케이션 내의 특정 에러 횟수** 등 사용자가 직접 정의한 데이터(Custom Metric)를 CloudWatch로 수집하여 모니터링하는 방법입니다.

## 2. 설명
* **수집 방법:**
  1. **CloudWatch Agent 설치:** EC2 내부 OS에 에이전트를 설치하고 `config.json`을 구성하여 메모리/디스크 지표를 자동으로 전송합니다.
  2. **AWS SDK/CLI 사용:** 개발자가 애플리케이션 코드 내에서 `put-metric-data` API를 호출하여 비즈니스 로직(예: 결제 실패 건수)을 직접 전송합니다.
* **해상도 (Resolution):**
  * 표준 해상도(Standard): 1분 단위 수집.
  * 고해상도(High-resolution): 1초 단위까지 세밀하게 수집 및 알람(Alarm) 설정 가능 (비용이 더 높음).

## 3. 참조 및 관련된 파일
* [[ec2-gpu-telemetry-capturing]] (GPU 메트릭 수집)
* [[cloudwatch-eks-fluentbit]]

## 4. 트러블 슈팅
* **커스텀 지표가 CloudWatch 콘솔에 나타나지 않음:**
  * **IAM 권한 부족:** 데이터를 보내는 주체(EC2의 IAM Role 또는 Lambda의 실행 역할)에 `cloudwatch:PutMetricData` 권한이 부여되어 있는지 확인합니다.
  * **네트워크 문제:** 프라이빗 서브넷에 있는 인스턴스라면, 인터넷으로 나가는 NAT Gateway가 있거나 CloudWatch를 위한 **VPC Endpoint (Interface)**가 설정되어 있어야 데이터를 전송할 수 있습니다.
* **CloudWatch 에이전트 시작 실패:** 에이전트 설정 파일의 JSON 문법 오류가 가장 흔한 원인입니다.

## 5. 참고자료 또는 링크
* [AWS 공식 문서 - CloudWatch 에이전트를 사용하여 지표 수집](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/Install-CloudWatch-Agent.html)

## TIP. 

cloudwatch agent로 메트릭을 수집하지 않는 경우(e.g. nvidia-smi 명령어로 GPU 상태 확인시),
서버 자체에서 custom metric sh를 생성하여, crontab을 등록해야한다. 

aws cloudwatch put-metric-data를 많이 생성해서 넘기는 것 보다는 json으로 한번에 보내야한다.
API 요청대로 요금이 발생하고, for문을 돌면서 계속해서 연결을 맺는 과정이 필요하기 때문에 속도도 느리다.

```bash
#!/bin/bash
REGION="ap-northeast-2"
TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
INSTANCE_ID=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/instance-id)
GPU_COUNT=$(nvidia-smi -L | wc -l)

# 메트릭 데이터를 담을 배열 초기화
METRICS_JSON="[]"

for (( i=0; i<$GPU_COUNT; i++ ))
do
    # [최적화 1] 단 한 번의 호출로 해당 GPU의 모든 텍스트를 변수에 저장
    GPU_INFO=$(nvidia-smi -i $i -q)

    # [최적화 2] 변수 내에서 텍스트 파싱 (추가 프로세스 실행 없음)
    UNCORR_VAL=$(echo "$GPU_INFO" | sed -n '/Aggregate/,/Retired/p' | grep "DRAM Uncorrectable" | awk '{print $NF}' | tr -dc '0-9')
    CORR_VAL=$(echo "$GPU_INFO" | sed -n '/Aggregate/,/Retired/p' | grep "DRAM Correctable" | awk '{print $NF}' | tr -dc '0-9')
    RETIRED_DBE=$(echo "$GPU_INFO" | grep -A 5 "Retired Pages" | grep "Double Bit ECC" | awk '{print $NF}' | tr -dc '0-9')

    # 빈 값 처리
    : ${UNCORR_VAL:=0}; : ${CORR_VAL:=0}; : ${RETIRED_DBE:=0}

    # [최적화 3] JSON 데이터 구조 생성 (메모리 내 누적)
    METRIC_DATA=$(cat <<EOF
[
  {"MetricName": "GPU_Aggregate_Uncorrectable_Errors", "Value": $UNCORR_VAL, "Unit": "Count", "Dimensions": [{"Name": "InstanceId", "Value": "$INSTANCE_ID"}, {"Name": "GPUIndex", "Value": "$i"}]},
  {"MetricName": "GPU_Aggregate_Correctable_Errors", "Value": $CORR_VAL, "Unit": "Count", "Dimensions": [{"Name": "InstanceId", "Value": "$INSTANCE_ID"}, {"Name": "GPUIndex", "Value": "$i"}]},
  {"MetricName": "GPU_Retired_Double_Bit_ECC", "Value": $RETIRED_DBE, "Unit": "Count", "Dimensions": [{"Name": "InstanceId", "Value": "$INSTANCE_ID"}, {"Name": "GPUIndex", "Value": "$i"}]}
]
EOF
)
    # 기존 JSON 배열에 합치기
    METRICS_JSON=$(echo "$METRICS_JSON" "$METRIC_DATA" | jq -s 'add')
done

# [최적화 4] 단 한 번의 네트워크 통신으로 모든 데이터 전송
aws cloudwatch put-metric-data --namespace "Custom/GPU_Health" --metric-data "$METRICS_JSON" --region "$REGION"

echo "모든 GPU($GPU_COUNT대)의 지표 전송 완료."
```
