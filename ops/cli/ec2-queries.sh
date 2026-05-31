#!/usr/bin/env bash
# EC2 실무 쿼리 모음
# 사용법: 필요한 함수만 복붙하거나, 전체 실행 시 ./ec2-queries.sh <함수명>

REGION="${AWS_DEFAULT_REGION:-ap-northeast-2}"

# ─── 인스턴스 목록 조회 ────────────────────────────────────────────────────────

# 실행 중인 인스턴스 전체 목록 (ID, 이름, 타입, IP, 상태)
list_running_instances() {
  aws ec2 describe-instances \
    --region "$REGION" \
    --filters "Name=instance-state-name,Values=running" \
    --query 'Reservations[].Instances[].[
      InstanceId,
      Tags[?Key==`Name`].Value | [0],
      InstanceType,
      PrivateIpAddress,
      PublicIpAddress,
      State.Name
    ]' \
    --output table
}

# 특정 태그로 인스턴스 필터링 (예: Environment=prod)
list_instances_by_tag() {
  local tag_key="${1:-Environment}"
  local tag_value="${2:-prod}"

  aws ec2 describe-instances \
    --region "$REGION" \
    --filters "Name=tag:${tag_key},Values=${tag_value}" \
               "Name=instance-state-name,Values=running" \
    --query 'Reservations[].Instances[].[InstanceId, Tags[?Key==`Name`].Value | [0], PrivateIpAddress, InstanceType]' \
    --output table
}

# 특정 인스턴스 타입 찾기
list_instances_by_type() {
  local instance_type="${1:-t3.medium}"

  aws ec2 describe-instances \
    --region "$REGION" \
    --filters "Name=instance-type,Values=${instance_type}" \
    --query 'Reservations[].Instances[].[InstanceId, Tags[?Key==`Name`].Value | [0], State.Name, PrivateIpAddress]' \
    --output table
}

# 중지된 인스턴스 목록 (비용 낭비 EBS 확인용)
list_stopped_instances() {
  aws ec2 describe-instances \
    --region "$REGION" \
    --filters "Name=instance-state-name,Values=stopped" \
    --query 'Reservations[].Instances[].[
      InstanceId,
      Tags[?Key==`Name`].Value | [0],
      InstanceType,
      LaunchTime,
      StateTransitionReason
    ]' \
    --output table
}

# ─── ASG / Launch Template ────────────────────────────────────────────────────

# ASG에 속한 인스턴스 목록
list_asg_instances() {
  local asg_name="${1:?ASG 이름을 입력하세요}"

  aws autoscaling describe-auto-scaling-groups \
    --region "$REGION" \
    --auto-scaling-group-names "$asg_name" \
    --query 'AutoScalingGroups[0].Instances[].[InstanceId, LifecycleState, HealthStatus, AvailabilityZone]' \
    --output table
}

# 모든 ASG 요약 (이름, 현재/최소/최대/원하는 용량)
list_all_asg() {
  aws autoscaling describe-auto-scaling-groups \
    --region "$REGION" \
    --query 'AutoScalingGroups[].[
      AutoScalingGroupName,
      MinSize,
      MaxSize,
      DesiredCapacity,
      length(Instances)
    ]' \
    --output table
}

# Launch Template 최신 버전 목록
list_launch_templates() {
  aws ec2 describe-launch-templates \
    --region "$REGION" \
    --query 'LaunchTemplates[].[LaunchTemplateName, LatestVersionNumber, DefaultVersionNumber, CreateTime]' \
    --output table
}

# ─── 네트워크 / IP ────────────────────────────────────────────────────────────

# EIP(탄력적 IP) 사용 현황 — 미연결 EIP는 비용 발생
list_eip_status() {
  aws ec2 describe-addresses \
    --region "$REGION" \
    --query 'Addresses[].[PublicIp, AllocationId, InstanceId, AssociationId, Domain]' \
    --output table
  echo ""
  echo "[미연결 EIP — 비용 발생 중]"
  aws ec2 describe-addresses \
    --region "$REGION" \
    --query 'Addresses[?AssociationId==null].[PublicIp, AllocationId]' \
    --output table
}

# 특정 인스턴스의 ENI 상세 정보
describe_instance_eni() {
  local instance_id="${1:?인스턴스 ID를 입력하세요}"

  aws ec2 describe-instances \
    --region "$REGION" \
    --instance-ids "$instance_id" \
    --query 'Reservations[0].Instances[0].NetworkInterfaces[].[
      NetworkInterfaceId,
      SubnetId,
      PrivateIpAddress,
      Association.PublicIp,
      Status
    ]' \
    --output table
}

# ─── 보안 그룹 ────────────────────────────────────────────────────────────────

# 0.0.0.0/0 인바운드 허용된 보안 그룹 탐지 (보안 감사)
find_open_security_groups() {
  echo "[경고] 0.0.0.0/0 인바운드 허용 보안 그룹:"
  aws ec2 describe-security-groups \
    --region "$REGION" \
    --query 'SecurityGroups[?IpPermissions[?IpRanges[?CidrIp==`0.0.0.0/0`]]].[GroupId, GroupName, Description]' \
    --output table
}

# 특정 보안 그룹에 연결된 인스턴스 조회
find_instances_by_sg() {
  local sg_id="${1:?보안 그룹 ID를 입력하세요 (sg-xxxxxxxx)}"

  aws ec2 describe-instances \
    --region "$REGION" \
    --filters "Name=instance.group-id,Values=${sg_id}" \
    --query 'Reservations[].Instances[].[InstanceId, Tags[?Key==`Name`].Value | [0], State.Name]' \
    --output table
}

# ─── EBS ─────────────────────────────────────────────────────────────────────

# 미연결(available) EBS 볼륨 목록 — 비용 낭비
list_unattached_ebs() {
  aws ec2 describe-volumes \
    --region "$REGION" \
    --filters "Name=status,Values=available" \
    --query 'Volumes[].[VolumeId, Size, VolumeType, CreateTime, AvailabilityZone]' \
    --output table
}

# gp2 볼륨 탐지 (gp3 마이그레이션 대상)
find_gp2_volumes() {
  aws ec2 describe-volumes \
    --region "$REGION" \
    --filters "Name=volume-type,Values=gp2" \
    --query 'Volumes[].[VolumeId, Size, State, Attachments[0].InstanceId]' \
    --output table
}

# ─── AMI / 스냅샷 ────────────────────────────────────────────────────────────

# 내 계정 AMI 목록
list_my_amis() {
  aws ec2 describe-images \
    --region "$REGION" \
    --owners self \
    --query 'Images[].[ImageId, Name, CreationDate, State]' \
    --output table
}

# 소유한 스냅샷 목록 (오래된 것 정리 목적)
list_my_snapshots() {
  local owner_id
  owner_id=$(aws sts get-caller-identity --query Account --output text)

  aws ec2 describe-snapshots \
    --region "$REGION" \
    --owner-ids "$owner_id" \
    --query 'Snapshots[].[SnapshotId, VolumeSize, StartTime, Description]' \
    --output table
}

# ─── 실행 진입점 ──────────────────────────────────────────────────────────────
case "${1:-}" in
  running)         list_running_instances ;;
  tag)             list_instances_by_tag "$2" "$3" ;;
  type)            list_instances_by_type "$2" ;;
  stopped)         list_stopped_instances ;;
  asg)             list_asg_instances "$2" ;;
  all-asg)         list_all_asg ;;
  lt)              list_launch_templates ;;
  eip)             list_eip_status ;;
  eni)             describe_instance_eni "$2" ;;
  open-sg)         find_open_security_groups ;;
  sg-instances)    find_instances_by_sg "$2" ;;
  unattached-ebs)  list_unattached_ebs ;;
  gp2)             find_gp2_volumes ;;
  amis)            list_my_amis ;;
  snapshots)       list_my_snapshots ;;
  *)
    echo "사용법: $0 <명령>"
    echo ""
    echo "  running          실행 중인 인스턴스 전체"
    echo "  tag KEY VALUE    태그로 인스턴스 필터"
    echo "  type TYPE        인스턴스 타입으로 필터"
    echo "  stopped          중지된 인스턴스"
    echo "  asg NAME         ASG 인스턴스 목록"
    echo "  all-asg          모든 ASG 요약"
    echo "  lt               Launch Template 목록"
    echo "  eip              EIP 사용 현황"
    echo "  eni INSTANCE_ID  ENI 상세"
    echo "  open-sg          0.0.0.0/0 허용 SG 탐지"
    echo "  sg-instances SG_ID  SG에 연결된 인스턴스"
    echo "  unattached-ebs   미연결 EBS"
    echo "  gp2              gp2 볼륨 목록"
    echo "  amis             내 AMI 목록"
    echo "  snapshots        내 스냅샷 목록"
    ;;
esac
