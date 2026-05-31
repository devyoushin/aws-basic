"""
SQS 메시지 배치 처리기
SQS 큐에서 메시지를 배치로 수신하여 처리하고,
실패한 메시지만 DLQ로 보내는 partial batch failure 패턴을 구현합니다.

트리거: SQS (Event Source Mapping)
  - Batch Size: 10 (최대 10,000)
  - Maximum Batching Window: 30초
  - Function Response Types: ReportBatchItemFailures  ← 반드시 설정

필요 IAM 권한:
  - sqs:ReceiveMessage
  - sqs:DeleteMessage
  - sqs:GetQueueAttributes
  - sqs:ChangeMessageVisibility

환경 변수:
  - PROCESSING_TYPE: "dynamodb" | "s3" | "http" | "log" (기본: log)
  - DEST_TABLE: DynamoDB 테이블 이름 (dynamodb 타입 시)
  - DEST_BUCKET: S3 버킷 이름 (s3 타입 시)
  - DEST_PREFIX: S3 경로 접두사 (기본: sqs-output/)
  - ENDPOINT_URL: HTTP 전송 대상 URL (http 타입 시)
  - MAX_RETRY_DELAY_SEC: 재처리 지연 (기본: 0)

Partial Batch Failure 패턴:
  처리 실패한 메시지의 messageId만 batchItemFailures에 포함시켜 반환하면
  Lambda가 해당 메시지만 큐로 돌려보내고 나머지는 삭제합니다.
  → 전체 배치 재처리로 인한 중복 처리 방지
"""

import boto3
import json
import logging
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")

PROCESSING_TYPE = os.environ.get("PROCESSING_TYPE", "log")
DEST_TABLE = os.environ.get("DEST_TABLE", "")
DEST_BUCKET = os.environ.get("DEST_BUCKET", "")
DEST_PREFIX = os.environ.get("DEST_PREFIX", "sqs-output/")
ENDPOINT_URL = os.environ.get("ENDPOINT_URL", "")


# ─── 메시지 파싱 ──────────────────────────────────────────────────────────────

def parse_message(sqs_record: dict) -> dict:
    """SQS 레코드에서 메시지 바디 파싱"""
    body = sqs_record["body"]
    message_id = sqs_record["messageId"]
    attributes = sqs_record.get("attributes", {})

    # SNS를 통해 온 메시지인 경우 한 번 더 언래핑
    try:
        parsed = json.loads(body)
        if isinstance(parsed, dict) and "Message" in parsed and "Type" in parsed:
            # SNS envelope
            inner = parsed["Message"]
            try:
                parsed = json.loads(inner)
            except json.JSONDecodeError:
                parsed = {"message": inner}
    except json.JSONDecodeError:
        parsed = {"raw": body}

    return {
        "message_id": message_id,
        "body": parsed,
        "approximate_receive_count": int(attributes.get("ApproximateReceiveCount", 1)),
        "sent_timestamp": attributes.get("SentTimestamp"),
        "received_at": datetime.now(timezone.utc).isoformat(),
    }


# ─── 처리 함수들 ──────────────────────────────────────────────────────────────

def process_to_dynamodb(message: dict) -> None:
    """DynamoDB에 메시지 저장"""
    if not DEST_TABLE:
        raise ValueError("DEST_TABLE 환경 변수 미설정")

    table = dynamodb.Table(DEST_TABLE)
    item = {
        "message_id": message["message_id"],
        "received_at": message["received_at"],
        "body": json.dumps(message["body"], ensure_ascii=False),
        "retry_count": message["approximate_receive_count"],
        "ttl": int(datetime.now(timezone.utc).timestamp()) + 86400 * 7,  # 7일 후 자동 삭제
    }

    # body가 dict이면 최상위 필드를 직접 저장 (검색 용이)
    if isinstance(message["body"], dict):
        for k, v in message["body"].items():
            if k not in item and isinstance(v, (str, int, float, bool)):
                item[k] = v

    table.put_item(Item=item)
    logger.debug("DynamoDB 저장: %s", message["message_id"])


def process_to_s3(message: dict) -> None:
    """S3에 메시지 저장 (날짜 파티셔닝)"""
    if not DEST_BUCKET:
        raise ValueError("DEST_BUCKET 환경 변수 미설정")

    now = datetime.now(timezone.utc)
    key = (
        f"{DEST_PREFIX}"
        f"{now.year}/{now.month:02d}/{now.day:02d}/"
        f"{now.hour:02d}/{message['message_id']}.json"
    )

    s3.put_object(
        Bucket=DEST_BUCKET,
        Key=key,
        Body=json.dumps(message, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )
    logger.debug("S3 저장: s3://%s/%s", DEST_BUCKET, key)


def process_to_http(message: dict) -> None:
    """HTTP 엔드포인트로 메시지 전달 (Webhook)"""
    if not ENDPOINT_URL:
        raise ValueError("ENDPOINT_URL 환경 변수 미설정")

    data = json.dumps(message["body"], ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        ENDPOINT_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-Message-Id": message["message_id"],
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"HTTP 오류: {resp.status}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP 전송 실패: {e.code} {e.read().decode()}")


def process_log_only(message: dict) -> None:
    """로깅만 수행 (테스트/개발용)"""
    logger.info(
        "메시지 처리: id=%s, retry=%d, body=%s",
        message["message_id"],
        message["approximate_receive_count"],
        json.dumps(message["body"], ensure_ascii=False)[:200],
    )


PROCESSORS = {
    "dynamodb": process_to_dynamodb,
    "s3": process_to_s3,
    "http": process_to_http,
    "log": process_log_only,
}


def process_single_message(sqs_record: dict) -> None:
    """단일 SQS 메시지 처리"""
    message = parse_message(sqs_record)

    processor = PROCESSORS.get(PROCESSING_TYPE, process_log_only)
    processor(message)


# ─── 핸들러 ───────────────────────────────────────────────────────────────────

def lambda_handler(event: dict, context) -> dict:
    """
    Partial Batch Failure 패턴
    - 성공한 메시지: 자동 삭제 (Lambda가 처리)
    - 실패한 메시지: batchItemFailures에 포함 → 큐로 반환 → DLQ로 이동

    주의: SQS Event Source Mapping에서
          "Report batch item failures" 옵션을 반드시 활성화해야 함
    """
    records = event.get("Records", [])
    logger.info("배치 수신: %d개", len(records))

    batch_item_failures = []
    success_count = 0

    for record in records:
        message_id = record["messageId"]

        try:
            process_single_message(record)
            success_count += 1

        except Exception as e:
            logger.error("메시지 처리 실패 [%s]: %s", message_id, str(e))
            # 실패한 메시지만 batchItemFailures에 추가
            batch_item_failures.append({"itemIdentifier": message_id})

    logger.info(
        "배치 완료: 성공=%d, 실패=%d/%d",
        success_count, len(batch_item_failures), len(records)
    )

    # batchItemFailures가 비어있으면 전체 성공 → SQS에서 모든 메시지 삭제
    # batchItemFailures에 포함된 메시지만 큐로 반환 (재처리 또는 DLQ)
    return {"batchItemFailures": batch_item_failures}
