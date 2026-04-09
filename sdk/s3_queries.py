"""
S3 실무 boto3 쿼리 모음
사용법: python s3_queries.py <명령> [인수]
"""

import boto3
import sys
import json
from datetime import datetime, timezone, timedelta
from typing import Optional, Generator
import hashlib
import os

session = boto3.Session(region_name="ap-northeast-2")
s3 = session.client("s3")
s3_resource = session.resource("s3")


# ─── 버킷 기본 조회 ───────────────────────────────────────────────────────────

def list_buckets() -> list[dict]:
    """전체 버킷 목록 + 리전"""
    resp = s3.list_buckets()
    buckets = []

    for bucket in resp["Buckets"]:
        try:
            region_resp = s3.get_bucket_location(Bucket=bucket["Name"])
            region = region_resp.get("LocationConstraint") or "us-east-1"
        except Exception:
            region = "unknown"

        buckets.append({
            "name": bucket["Name"],
            "created": bucket["CreationDate"].strftime("%Y-%m-%d"),
            "region": region,
        })

    return buckets


def get_bucket_size(bucket: str, prefix: str = "") -> dict:
    """버킷(또는 프리픽스) 크기 및 오브젝트 수"""
    paginator = s3.get_paginator("list_objects_v2")
    total_size = 0
    total_count = 0

    kwargs = {"Bucket": bucket}
    if prefix:
        kwargs["Prefix"] = prefix

    for page in paginator.paginate(**kwargs):
        for obj in page.get("Contents", []):
            total_size += obj["Size"]
            total_count += 1

    return {
        "bucket": bucket,
        "prefix": prefix or "(전체)",
        "object_count": total_count,
        "total_size_bytes": total_size,
        "total_size_mb": round(total_size / 1024 / 1024, 2),
        "total_size_gb": round(total_size / 1024 / 1024 / 1024, 4),
    }


def list_objects(
    bucket: str,
    prefix: str = "",
    limit: int = 20,
    modified_after: Optional[datetime] = None,
) -> list[dict]:
    """오브젝트 목록 (최근 수정순)"""
    paginator = s3.get_paginator("list_objects_v2")
    objects = []

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if modified_after and obj["LastModified"] < modified_after:
                continue
            objects.append({
                "key": obj["Key"],
                "size_kb": round(obj["Size"] / 1024, 1),
                "last_modified": obj["LastModified"].strftime("%Y-%m-%d %H:%M"),
                "storage_class": obj.get("StorageClass", "STANDARD"),
            })

    return sorted(objects, key=lambda x: x["last_modified"], reverse=True)[:limit]


def get_presigned_url(bucket: str, key: str, expires_in: int = 3600) -> str:
    """Pre-signed URL 생성 (기본 1시간)"""
    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires_in,
    )
    return url


# ─── 보안 점검 ────────────────────────────────────────────────────────────────

def check_bucket_security(bucket: str) -> dict:
    """버킷 보안 설정 종합 점검"""
    result = {"bucket": bucket}

    # 퍼블릭 액세스 차단
    try:
        pab = s3.get_public_access_block(Bucket=bucket)["PublicAccessBlockConfiguration"]
        result["public_access_block"] = {
            "BlockPublicAcls": pab.get("BlockPublicAcls", False),
            "BlockPublicPolicy": pab.get("BlockPublicPolicy", False),
            "IgnorePublicAcls": pab.get("IgnorePublicAcls", False),
            "RestrictPublicBuckets": pab.get("RestrictPublicBuckets", False),
        }
        result["fully_blocked"] = all(result["public_access_block"].values())
    except s3.exceptions.NoSuchPublicAccessBlockConfiguration:
        result["public_access_block"] = "미설정"
        result["fully_blocked"] = False

    # 암호화
    try:
        enc = s3.get_bucket_encryption(Bucket=bucket)
        rules = enc["ServerSideEncryptionConfiguration"]["Rules"]
        result["encryption"] = rules[0]["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"]
    except Exception:
        result["encryption"] = "미설정"

    # 버전 관리
    try:
        ver = s3.get_bucket_versioning(Bucket=bucket)
        result["versioning"] = ver.get("Status", "미설정")
    except Exception:
        result["versioning"] = "unknown"

    # 버킷 정책 존재 여부
    try:
        s3.get_bucket_policy(Bucket=bucket)
        result["has_policy"] = True
    except s3.exceptions.from_code("NoSuchBucketPolicy"):
        result["has_policy"] = False
    except Exception:
        result["has_policy"] = "unknown"

    # 로깅
    try:
        logging_resp = s3.get_bucket_logging(Bucket=bucket)
        result["logging_enabled"] = "LoggingEnabled" in logging_resp
    except Exception:
        result["logging_enabled"] = False

    return result


def audit_all_buckets() -> list[dict]:
    """전체 버킷 보안 감사"""
    resp = s3.list_buckets()
    results = []

    for bucket in resp["Buckets"]:
        try:
            sec = check_bucket_security(bucket["Name"])
            results.append({
                "bucket": bucket["Name"],
                "fully_blocked": sec["fully_blocked"],
                "encryption": sec["encryption"],
                "versioning": sec["versioning"],
                "logging": sec["logging_enabled"],
                "has_policy": sec.get("has_policy", "-"),
            })
        except Exception as e:
            results.append({"bucket": bucket["Name"], "error": str(e)})

    return results


# ─── 파일 업로드 / 다운로드 ───────────────────────────────────────────────────

def upload_file(
    local_path: str,
    bucket: str,
    s3_key: str,
    extra_args: Optional[dict] = None,
) -> None:
    """
    파일 업로드 (멀티파트 자동 처리)
    extra_args 예시: {"ServerSideEncryption": "AES256", "ContentType": "application/json"}
    """
    file_size = os.path.getsize(local_path)
    print(f"업로드 중: {local_path} → s3://{bucket}/{s3_key} ({file_size / 1024 / 1024:.1f} MB)")

    kwargs = {"ExtraArgs": extra_args} if extra_args else {}
    s3_resource.Bucket(bucket).upload_file(local_path, s3_key, **kwargs)
    print("[업로드 완료]")


def download_file(bucket: str, s3_key: str, local_path: str) -> None:
    """파일 다운로드"""
    print(f"다운로드 중: s3://{bucket}/{s3_key} → {local_path}")
    s3_resource.Bucket(bucket).download_file(s3_key, local_path)
    print("[다운로드 완료]")


def upload_with_checksum(local_path: str, bucket: str, s3_key: str) -> str:
    """SHA-256 체크섬 검증 포함 업로드"""
    with open(local_path, "rb") as f:
        content = f.read()
        sha256 = hashlib.sha256(content).hexdigest()

    s3.put_object(
        Bucket=bucket,
        Key=s3_key,
        Body=content,
        ChecksumAlgorithm="SHA256",
    )
    print(f"[업로드 완료] SHA-256: {sha256}")
    return sha256


# ─── 데이터 추출 / 변환 ───────────────────────────────────────────────────────

def read_json_object(bucket: str, key: str) -> dict | list:
    """S3의 JSON 파일을 직접 읽어서 Python 객체로 반환"""
    resp = s3.get_object(Bucket=bucket, Key=key)
    return json.loads(resp["Body"].read().decode("utf-8"))


def stream_large_file(bucket: str, key: str, chunk_size: int = 8 * 1024 * 1024) -> Generator:
    """대용량 파일 스트리밍 읽기 (메모리 효율)"""
    resp = s3.get_object(Bucket=bucket, Key=key)
    stream = resp["Body"]

    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            break
        yield chunk


def s3_select_csv(
    bucket: str,
    key: str,
    sql: str,
    header: bool = True,
) -> list[str]:
    """
    S3 Select로 CSV에서 SQL 조회 (전체 다운로드 없이 필터링)
    예: sql = "SELECT * FROM s3object WHERE s.age > 30"
    """
    resp = s3.select_object_content(
        Bucket=bucket,
        Key=key,
        ExpressionType="SQL",
        Expression=sql,
        InputSerialization={
            "CSV": {
                "FileHeaderInfo": "USE" if header else "NONE",
                "RecordDelimiter": "\n",
                "FieldDelimiter": ",",
            },
            "CompressionType": "NONE",
        },
        OutputSerialization={"CSV": {}},
    )

    results = []
    for event in resp["Payload"]:
        if "Records" in event:
            results.extend(
                event["Records"]["Payload"].decode("utf-8").strip().split("\n")
            )

    return results


# ─── Lifecycle / 비용 최적화 ─────────────────────────────────────────────────

def get_lifecycle_rules(bucket: str) -> list[dict]:
    """Lifecycle 규칙 확인"""
    try:
        resp = s3.get_bucket_lifecycle_configuration(Bucket=bucket)
        return resp.get("Rules", [])
    except s3.exceptions.from_code("NoSuchLifecycleConfiguration"):
        return []


def put_lifecycle_rule(
    bucket: str,
    rule_id: str,
    prefix: str = "",
    ia_days: int = 30,
    glacier_days: int = 90,
    expire_days: Optional[int] = None,
) -> None:
    """
    Lifecycle 규칙 설정
    ia_days: STANDARD_IA 전환 일수
    glacier_days: Glacier 전환 일수
    expire_days: 삭제 일수 (None이면 삭제 안 함)
    """
    transitions = []
    if ia_days:
        transitions.append({"Days": ia_days, "StorageClass": "STANDARD_IA"})
    if glacier_days:
        transitions.append({"Days": glacier_days, "StorageClass": "GLACIER"})

    rule = {
        "ID": rule_id,
        "Status": "Enabled",
        "Filter": {"Prefix": prefix},
        "Transitions": transitions,
    }
    if expire_days:
        rule["Expiration"] = {"Days": expire_days}

    # 기존 규칙 유지하면서 추가
    existing_rules = get_lifecycle_rules(bucket)
    existing_rules = [r for r in existing_rules if r["ID"] != rule_id]
    existing_rules.append(rule)

    s3.put_bucket_lifecycle_configuration(
        Bucket=bucket,
        LifecycleConfiguration={"Rules": existing_rules},
    )
    print(f"[Lifecycle 설정 완료] {bucket}/{prefix or '(전체)'}")


# ─── CLI 실행 ─────────────────────────────────────────────────────────────────

def print_table(data: list[dict]) -> None:
    if not data:
        print("(결과 없음)")
        return
    keys = list(data[0].keys())
    widths = {k: max(len(k), max(len(str(row.get(k, ""))) for row in data)) for k in keys}
    header = "  ".join(k.ljust(widths[k]) for k in keys)
    print(header)
    print("-" * len(header))
    for row in data:
        print("  ".join(str(row.get(k, "")).ljust(widths[k]) for k in keys))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""

    if cmd == "buckets":
        print_table(list_buckets())
    elif cmd == "size":
        result = get_bucket_size(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "")
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif cmd == "objects":
        print_table(list_objects(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else ""))
    elif cmd == "security":
        result = check_bucket_security(sys.argv[2])
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif cmd == "audit":
        print_table(audit_all_buckets())
    elif cmd == "presign":
        expires = int(sys.argv[4]) if len(sys.argv) > 4 else 3600
        print(get_presigned_url(sys.argv[2], sys.argv[3], expires))
    elif cmd == "read-json":
        data = read_json_object(sys.argv[2], sys.argv[3])
        print(json.dumps(data, indent=2, ensure_ascii=False))
    elif cmd == "lifecycle":
        rules = get_lifecycle_rules(sys.argv[2])
        print(json.dumps(rules, indent=2, default=str, ensure_ascii=False))
    else:
        print("사용법: python s3_queries.py <명령> [인수]\n")
        print("  buckets                      버킷 목록")
        print("  size BUCKET [PREFIX]          버킷 크기")
        print("  objects BUCKET [PREFIX]       오브젝트 목록")
        print("  security BUCKET               보안 설정 점검")
        print("  audit                         전체 버킷 보안 감사")
        print("  presign BUCKET KEY [SECONDS]  Pre-signed URL")
        print("  read-json BUCKET KEY          JSON 파일 읽기")
        print("  lifecycle BUCKET              Lifecycle 규칙")
