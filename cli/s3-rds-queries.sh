#!/usr/bin/env bash
# S3 / RDS 실무 쿼리 모음
# 사용법: ./s3-rds-queries.sh <명령> [인수]

REGION="${AWS_DEFAULT_REGION:-ap-northeast-2}"

# ══════════════════════════════════════════════════
# S3
# ══════════════════════════════════════════════════

# ─── 버킷 목록 / 기본 정보 ───────────────────────────────────────────────────

# 전체 버킷 목록 + 리전
list_buckets() {
  aws s3api list-buckets \
    --query 'Buckets[].[Name, CreationDate]' \
    --output table
}

# 특정 버킷 내 오브젝트 수 및 총 크기
get_bucket_size() {
  local bucket="${1:?버킷 이름을 입력하세요}"

  aws s3 ls "s3://$bucket" --recursive --human-readable --summarize 2>/dev/null | tail -2
}

# 버킷 내 특정 프리픽스 하위 파일 목록 (최근 수정순)
list_objects_by_prefix() {
  local bucket="${1:?버킷 이름을 입력하세요}"
  local prefix="${2:-}"
  local max="${3:-20}"

  aws s3api list-objects-v2 \
    --bucket "$bucket" \
    --prefix "$prefix" \
    --query "sort_by(Contents, &LastModified) | reverse(@) | [:${max}].[Key, Size, LastModified]" \
    --output table
}

# 특정 날짜 이후 수정된 오브젝트 (변경 탐지)
list_objects_modified_after() {
  local bucket="${1:?버킷 이름을 입력하세요}"
  local date="${2:?날짜를 입력하세요 (예: 2025-01-01)}"

  aws s3api list-objects-v2 \
    --bucket "$bucket" \
    --query "Contents[?LastModified>='${date}'].[Key, Size, LastModified]" \
    --output table
}

# ─── 보안 설정 점검 ───────────────────────────────────────────────────────────

# 퍼블릭 액세스 차단 설정 확인
check_public_access_block() {
  local bucket="${1:?버킷 이름을 입력하세요}"

  aws s3api get-public-access-block \
    --bucket "$bucket" \
    --query 'PublicAccessBlockConfiguration' \
    --output table
}

# 퍼블릭 차단 미설정 버킷 전체 탐색 (보안 감사)
find_public_buckets() {
  echo "[퍼블릭 액세스 차단 미설정 버킷]"
  aws s3api list-buckets --query 'Buckets[].Name' --output text | tr '\t' '\n' | while read -r bucket; do
    local block_all
    block_all=$(aws s3api get-public-access-block --bucket "$bucket" \
      --query 'PublicAccessBlockConfiguration.BlockPublicAcls' \
      --output text 2>/dev/null)
    if [[ "$block_all" != "True" ]]; then
      echo "  $bucket (BlockPublicAcls=$block_all)"
    fi
  done
}

# 버킷 정책 확인
get_bucket_policy() {
  local bucket="${1:?버킷 이름을 입력하세요}"

  aws s3api get-bucket-policy \
    --bucket "$bucket" \
    --query 'Policy' \
    --output text | python3 -m json.tool
}

# 버킷 암호화 설정 확인
get_bucket_encryption() {
  local bucket="${1:?버킷 이름을 입력하세요}"

  aws s3api get-bucket-encryption \
    --bucket "$bucket" \
    --query 'ServerSideEncryptionConfiguration' \
    --output table
}

# 버전 관리 상태 확인
get_bucket_versioning() {
  local bucket="${1:?버킷 이름을 입력하세요}"

  aws s3api get-bucket-versioning \
    --bucket "$bucket" \
    --output table
}

# ─── Lifecycle / 스토리지 클래스 ─────────────────────────────────────────────

# Lifecycle 규칙 확인
get_lifecycle_rules() {
  local bucket="${1:?버킷 이름을 입력하세요}"

  aws s3api get-bucket-lifecycle-configuration \
    --bucket "$bucket" \
    --output json | python3 -m json.tool
}

# 특정 버킷의 스토리지 클래스별 분포 (Intelligent-Tiering 확인)
list_objects_by_storage_class() {
  local bucket="${1:?버킷 이름을 입력하세요}"

  aws s3api list-objects-v2 \
    --bucket "$bucket" \
    --query 'Contents[].[StorageClass]' \
    --output text | sort | uniq -c | sort -rn
}

# ══════════════════════════════════════════════════
# RDS
# ══════════════════════════════════════════════════

# ─── 인스턴스 / 클러스터 ─────────────────────────────────────────────────────

# RDS 인스턴스 전체 목록
list_rds_instances() {
  aws rds describe-db-instances \
    --region "$REGION" \
    --query 'DBInstances[].[
      DBInstanceIdentifier,
      DBInstanceClass,
      Engine,
      EngineVersion,
      DBInstanceStatus,
      MultiAZ,
      Endpoint.Address
    ]' \
    --output table
}

# Aurora 클러스터 목록 (Writer/Reader 구분)
list_aurora_clusters() {
  aws rds describe-db-clusters \
    --region "$REGION" \
    --query 'DBClusters[].[
      DBClusterIdentifier,
      Engine,
      EngineVersion,
      Status,
      MultiAZ,
      Endpoint,
      ReaderEndpoint
    ]' \
    --output table
}

# 특정 클러스터 멤버 (Writer/Reader 인스턴스 역할)
describe_cluster_members() {
  local cluster="${1:?클러스터 이름을 입력하세요}"

  aws rds describe-db-clusters \
    --region "$REGION" \
    --db-cluster-identifier "$cluster" \
    --query 'DBClusters[0].DBClusterMembers[].[DBInstanceIdentifier, IsClusterWriter, DBClusterParameterGroupStatus]' \
    --output table
}

# ─── 파라미터 그룹 ───────────────────────────────────────────────────────────

# 파라미터 그룹 목록
list_parameter_groups() {
  aws rds describe-db-parameter-groups \
    --region "$REGION" \
    --query 'DBParameterGroups[].[DBParameterGroupName, DBParameterGroupFamily, Description]' \
    --output table
}

# 특정 파라미터 값 확인 (예: slow_query_log)
get_parameter_value() {
  local param_group="${1:?파라미터 그룹 이름을 입력하세요}"
  local param_name="${2:-slow_query_log}"

  aws rds describe-db-parameters \
    --region "$REGION" \
    --db-parameter-group-name "$param_group" \
    --query "Parameters[?ParameterName=='${param_name}'].[ParameterName, ParameterValue, ApplyType]" \
    --output table
}

# ─── 스냅샷 / 백업 ───────────────────────────────────────────────────────────

# 자동 스냅샷 목록 (최근 5개)
list_rds_snapshots() {
  local db_id="${1:?DB 인스턴스 이름을 입력하세요}"

  aws rds describe-db-snapshots \
    --region "$REGION" \
    --db-instance-identifier "$db_id" \
    --snapshot-type automated \
    --query 'sort_by(DBSnapshots, &SnapshotCreateTime) | reverse(@) | [:5].[DBSnapshotIdentifier, SnapshotCreateTime, AllocatedStorage, Status]' \
    --output table
}

# 백업 보존 기간 확인
check_backup_retention() {
  aws rds describe-db-instances \
    --region "$REGION" \
    --query 'DBInstances[].[DBInstanceIdentifier, BackupRetentionPeriod, PreferredBackupWindow]' \
    --output table
}

# 백업 보존 기간 0인 인스턴스 탐지 (백업 비활성화)
find_no_backup_instances() {
  echo "[백업 미설정 RDS 인스턴스 (BackupRetentionPeriod=0)]"
  aws rds describe-db-instances \
    --region "$REGION" \
    --query 'DBInstances[?BackupRetentionPeriod==`0`].[DBInstanceIdentifier, DBInstanceClass, Engine]' \
    --output table
}

# ─── 실행 진입점 ──────────────────────────────────────────────────────────────
case "${1:-}" in
  # S3
  buckets)            list_buckets ;;
  bucket-size)        get_bucket_size "$2" ;;
  objects)            list_objects_by_prefix "$2" "$3" "$4" ;;
  objects-after)      list_objects_modified_after "$2" "$3" ;;
  public-block)       check_public_access_block "$2" ;;
  find-public)        find_public_buckets ;;
  bucket-policy)      get_bucket_policy "$2" ;;
  encryption)         get_bucket_encryption "$2" ;;
  versioning)         get_bucket_versioning "$2" ;;
  lifecycle)          get_lifecycle_rules "$2" ;;
  storage-class)      list_objects_by_storage_class "$2" ;;
  # RDS
  rds)                list_rds_instances ;;
  aurora)             list_aurora_clusters ;;
  cluster-members)    describe_cluster_members "$2" ;;
  param-groups)       list_parameter_groups ;;
  param)              get_parameter_value "$2" "$3" ;;
  snapshots)          list_rds_snapshots "$2" ;;
  backup-retention)   check_backup_retention ;;
  no-backup)          find_no_backup_instances ;;
  *)
    echo "사용법: $0 <명령> [인수]"
    echo ""
    echo "=== S3 ==="
    echo "  buckets                      버킷 목록"
    echo "  bucket-size BUCKET           버킷 크기"
    echo "  objects BUCKET [PREFIX] [N]  오브젝트 목록"
    echo "  objects-after BUCKET DATE    특정 날짜 이후 변경"
    echo "  public-block BUCKET          퍼블릭 차단 설정"
    echo "  find-public                  퍼블릭 버킷 탐색"
    echo "  bucket-policy BUCKET         버킷 정책"
    echo "  encryption BUCKET            암호화 설정"
    echo "  versioning BUCKET            버전 관리 상태"
    echo "  lifecycle BUCKET             Lifecycle 규칙"
    echo "  storage-class BUCKET         스토리지 클래스 분포"
    echo ""
    echo "=== RDS ==="
    echo "  rds                          RDS 인스턴스 목록"
    echo "  aurora                       Aurora 클러스터 목록"
    echo "  cluster-members CLUSTER      클러스터 멤버 역할"
    echo "  param-groups                 파라미터 그룹 목록"
    echo "  param GROUP [PARAM]          파라미터 값 확인"
    echo "  snapshots DB_ID              스냅샷 목록"
    echo "  backup-retention             백업 보존 기간"
    echo "  no-backup                    백업 미설정 인스턴스"
    ;;
esac
