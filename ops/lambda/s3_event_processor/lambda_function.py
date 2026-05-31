"""
S3 이벤트 처리기 — 파일 업로드 시 자동 처리
S3 버킷에 파일이 업로드되면 Lambda가 호출되어 후처리를 수행합니다.

트리거: S3 Event Notification (ObjectCreated:*)

지원하는 처리 유형:
  - CSV 파싱 후 DynamoDB 저장
  - JSON 로그 분석 및 요약
  - 이미지 업로드 감지 및 메타데이터 기록
  - 파일 이동 (quarantine / processed 경로 분리)

필요 IAM 권한:
  - s3:GetObject
  - s3:PutObject
  - s3:DeleteObject (이동 처리 시)
  - dynamodb:PutItem, BatchWriteItem (DynamoDB 저장 시)

환경 변수:
  - DEST_BUCKET: 처리 완료 파일을 이동할 버킷 (비워두면 이동 안 함)
  - DEST_PREFIX: 처리 완료 파일 경로 접두사 (기본: processed/)
  - ERROR_PREFIX: 실패 파일 경로 접두사 (기본: error/)
  - DYNAMODB_TABLE: CSV 데이터를 저장할 DynamoDB 테이블 (선택)
  - MAX_FILE_SIZE_MB: 처리할 최대 파일 크기 (기본: 50)
"""

import boto3
import csv
import gzip
import io
import json
import logging
import os
import urllib.parse
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")

DEST_BUCKET = os.environ.get("DEST_BUCKET", "")
DEST_PREFIX = os.environ.get("DEST_PREFIX", "processed/")
ERROR_PREFIX = os.environ.get("ERROR_PREFIX", "error/")
DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE", "")
MAX_FILE_SIZE_MB = int(os.environ.get("MAX_FILE_SIZE_MB", "50"))


# ─── 파일 읽기 ────────────────────────────────────────────────────────────────

def read_s3_object(bucket: str, key: str) -> tuple[bytes, dict]:
    """S3 오브젝트 읽기 (gzip 자동 해제)"""
    resp = s3.get_object(Bucket=bucket, Key=key)

    content_length = resp.get("ContentLength", 0)
    if content_length > MAX_FILE_SIZE_MB * 1024 * 1024:
        raise ValueError(f"파일 크기 초과: {content_length / 1024 / 1024:.1f} MB > {MAX_FILE_SIZE_MB} MB")

    body = resp["Body"].read()

    # gzip 자동 해제
    if key.endswith(".gz"):
        body = gzip.decompress(body)

    metadata = {
        "content_type": resp.get("ContentType", ""),
        "size": content_length,
        "last_modified": resp["LastModified"].isoformat(),
    }

    return body, metadata


# ─── 처리 함수들 ──────────────────────────────────────────────────────────────

def process_csv(bucket: str, key: str, body: bytes) -> dict:
    """
    CSV 파일 파싱 후 DynamoDB 저장
    CSV 첫 행을 헤더로 사용
    """
    rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))

    if not rows:
        return {"type": "csv", "row_count": 0}

    logger.info("CSV 파싱 완료: %d행, 컬럼: %s", len(rows), list(rows[0].keys()))

    # DynamoDB 배치 저장
    if DYNAMODB_TABLE:
        table = dynamodb.Table(DYNAMODB_TABLE)
        saved = 0

        # 25개씩 배치 저장 (DynamoDB 제한)
        for i in range(0, len(rows), 25):
            batch = rows[i:i + 25]
            with table.batch_writer() as writer:
                for row in batch:
                    # partition key가 없을 경우 메타데이터 추가
                    row["_source"] = f"s3://{bucket}/{key}"
                    row["_imported_at"] = datetime.now(timezone.utc).isoformat()
                    writer.put_item(Item=row)
            saved += len(batch)

        logger.info("DynamoDB 저장: %d/%d건", saved, len(rows))

    return {"type": "csv", "row_count": len(rows), "columns": list(rows[0].keys())}


def process_json_logs(bucket: str, key: str, body: bytes) -> dict:
    """JSON Lines 로그 분석 및 요약"""
    lines = [line for line in body.decode("utf-8").splitlines() if line.strip()]
    records = []
    parse_errors = 0

    for line in lines:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            parse_errors += 1

    # 레벨별 집계
    level_counts: dict[str, int] = {}
    for record in records:
        level = record.get("level", record.get("severity", "UNKNOWN")).upper()
        level_counts[level] = level_counts.get(level, 0) + 1

    summary = {
        "type": "json_logs",
        "total_lines": len(lines),
        "parsed_records": len(records),
        "parse_errors": parse_errors,
        "level_counts": level_counts,
    }

    # 에러 레코드 샘플 (최대 5개) 를 별도 저장
    error_records = [r for r in records if r.get("level", "").upper() in ("ERROR", "CRITICAL")]
    if error_records:
        summary["error_sample_count"] = len(error_records)
        error_key = f"{ERROR_PREFIX}error_sample/{key.split('/')[-1]}.errors.json"
        s3.put_object(
            Bucket=bucket,
            Key=error_key,
            Body=json.dumps(error_records[:5], ensure_ascii=False).encode("utf-8"),
            ContentType="application/json",
        )
        summary["error_sample_key"] = error_key

    return summary


def process_generic(bucket: str, key: str, body: bytes) -> dict:
    """기본 처리: 파일 크기, 해시 등 메타데이터만 기록"""
    import hashlib

    return {
        "type": "generic",
        "size_bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "first_bytes": body[:100].decode("utf-8", errors="replace"),
    }


# ─── 파일 이동 ────────────────────────────────────────────────────────────────

def move_to_processed(src_bucket: str, src_key: str, result: dict) -> Optional[str]:
    """처리 완료 파일을 DEST_BUCKET/processed/ 로 이동"""
    if not DEST_BUCKET:
        return None

    dest_key = f"{DEST_PREFIX}{src_key}"

    # 처리 결과를 메타데이터로 첨부
    s3.copy_object(
        CopySource={"Bucket": src_bucket, "Key": src_key},
        Bucket=DEST_BUCKET,
        Key=dest_key,
        Metadata={
            "processed-at": datetime.now(timezone.utc).isoformat(),
            "original-bucket": src_bucket,
            "original-key": src_key,
            "process-result": json.dumps(result)[:1024],  # 메타데이터 크기 제한
        },
        MetadataDirective="REPLACE",
    )

    # 원본 삭제
    s3.delete_object(Bucket=src_bucket, Key=src_key)
    logger.info("이동 완료: s3://%s/%s → s3://%s/%s", src_bucket, src_key, DEST_BUCKET, dest_key)

    return f"s3://{DEST_BUCKET}/{dest_key}"


# ─── 메인 핸들러 ──────────────────────────────────────────────────────────────

def process_record(bucket: str, key: str) -> dict:
    """단일 S3 오브젝트 처리"""
    logger.info("처리 시작: s3://%s/%s", bucket, key)

    body, metadata = read_s3_object(bucket, key)
    lower_key = key.lower()

    # 파일 유형에 따라 처리 분기
    if lower_key.endswith((".csv", ".csv.gz", ".tsv")):
        result = process_csv(bucket, key, body)
    elif lower_key.endswith((".jsonl", ".ndjson", ".log", ".log.gz")):
        result = process_json_logs(bucket, key, body)
    else:
        result = process_generic(bucket, key, body)

    result["source"] = f"s3://{bucket}/{key}"
    result["metadata"] = metadata
    result["processed_at"] = datetime.now(timezone.utc).isoformat()

    # 처리 완료 파일 이동
    dest = move_to_processed(bucket, key, result)
    if dest:
        result["moved_to"] = dest

    return result


def lambda_handler(event: dict, context) -> dict:
    logger.info("이벤트 수신: %d개 레코드", len(event.get("Records", [])))

    results = []
    errors = []

    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])

        try:
            result = process_record(bucket, key)
            results.append(result)
            logger.info("처리 완료: %s", json.dumps(result, ensure_ascii=False))

        except Exception as e:
            error = {"bucket": bucket, "key": key, "error": str(e)}
            errors.append(error)
            logger.error("처리 실패: %s", json.dumps(error))

            # 실패한 파일을 error/ 경로로 이동
            try:
                s3.copy_object(
                    CopySource={"Bucket": bucket, "Key": key},
                    Bucket=bucket,
                    Key=f"{ERROR_PREFIX}{key}",
                    Metadata={"error": str(e)[:512]},
                    MetadataDirective="REPLACE",
                )
            except Exception as move_err:
                logger.error("에러 파일 이동 실패: %s", move_err)

    return {
        "statusCode": 200 if not errors else 207,
        "processed": len(results),
        "failed": len(errors),
        "results": results,
        "errors": errors,
    }
