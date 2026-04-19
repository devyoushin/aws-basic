## 1. 개요
기계 학습(ML)이나 고성능 컴퓨팅(HPC)을 위해 사용하는 **GPU 기반 EC2 인스턴스(P, G 패밀리 등)의 GPU 활용도, 메모리 사용량, 온도 등의 텔레메트리(원격 측정) 데이터를 CloudWatch로 수집**하는 방법입니다.

## 2. 설명
* **기본 지표의 한계:** AWS가 기본적으로 제공하는 EC2 CloudWatch 지표는 CPU, 네트워크, 디스크 I/O에 국한되며 GPU 지표는 포함되지 않습니다.
* **수집 방법:**
  1. **CloudWatch 에이전트 + NVIDIA DCGM:** NVIDIA Data Center GPU Manager(DCGM)를 설치하고 CloudWatch 에이전트와 연동하여 커스텀 메트릭으로 푸시합니다.
  2. **AWS Deep Learning AMI (DLAMI):** 사전에 GPU 드라이버와 텔레메트리 수집 스크립트가 세팅된 DLAMI를 사용하면 더 쉽게 구성할 수 있습니다.
* **주요 모니터링 지표:** `gpu_utilization`(GPU 코어 사용률), `gpu_memory_utilization`(VRAM 사용률), `gpu_temperature`(온도).

## 3. 트러블 슈팅
* **GPU 지표가 수집되지 않는 현상:**
  * NVIDIA 드라이버가 인스턴스 커널 버전과 호환되지 않아 로드되지 않았을 수 있습니다.

## TIP
ec2 instance에서 gpu의 이상현상(e.g. AWS 호스트 서버의 문제)으로 확인하는 방법은 아래의 문서를 참고하면 좋다.
특히 `dmesg -T | grep NVRM` 명령어를 통해 아래의 xid 이슈가 있다면 호스트 서버가 변경될 수 있도록
중지 및 시작 작업을 진행하는 것이 좋다.

|   |   |   |   |
|---|---|---|---|
|**Xid Error**|**Name**|**Description**|**Action**|
|48|Double Bit ECC error|Hardware memory error|Contact AWS Support with Xid error and instance ID|
|74|GPU NVLink error|Further SXid errors should also be populated which will inform on the error seen with the NVLink fabric|Get information on which links are causing the issue by running nvidia-smi nvlink -e|
|63|GPU Row Remapping Event|Specific to Ampere architecture –- a row bank is pending a memory remap|Stop all CUDA processes, and reset the GPU (nvidia-smi -r), and make sure thatensure the remap is cleared in nvidia-smi -q|
|13|Graphics Engine Exception|User application fault , illegal instruction or register|Rerun the application with CUDA_LAUNCH_BLOCKING=1 enabled which should determine if it’s a NVIDIA driver or hardware issue|
|31|GPU memory page fault|Illegal memory address access error|Rerun the application with CUDA_LAUNCH_BLOCKING=1 enabled which should determine if it’s a NVIDIA driver or hardware issue|
- **[참고]GPU 메트릭 변화 파악하기**
https://aws.amazon.com/ko/blogs/compute/capturing-gpu-telemetry-on-the-amazon-ec2-accelerated-computing-instances/
