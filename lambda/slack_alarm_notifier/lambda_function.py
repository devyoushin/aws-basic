"""
CloudWatch Alarm → SNS → Lambda → Slack 알림 발송
CloudWatch Alarm이 발생하면 SNS를 통해 이 Lambda가 호출되어 Slack으로 포맷된 메시지를 전송합니다.

트리거: SNS (CloudWatch Alarm 연동)
  CloudWatch Alarm → SNS Topic → Lambda

필요 IAM 권한:
  - secretsmanager:GetSecretValue (Slack Webhook URL을 Secrets Manager에 저장한 경우)

환경 변수:
  - SLACK_WEBHOOK_URL: Slack Incoming Webhook URL
    또는
  - SLACK_SECRET_NAME: Secrets Manager에 저장된 시크릿 이름

Secrets Manager 시크릿 구조:
  {"webhook_url": "https://hooks.slack.com/services/..."}

SNS 메시지 구조 (CloudWatch Alarm):
  {
    "AlarmName": "...",
    "AlarmDescription": "...",
    "NewStateValue": "ALARM|OK|INSUFFICIENT_DATA",
    "OldStateValue": "...",
    "NewStateReason": "...",
    "StateChangeTime": "...",
    "Region": "...",
    "AWSAccountId": "...",
    "Trigger": {...}
  }
"""

import boto3
import json
import logging
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
SLACK_SECRET_NAME = os.environ.get("SLACK_SECRET_NAME", "")

# 알람 상태별 색상 및 이모지
STATE_CONFIG = {
    "ALARM": {"color": "#FF0000", "emoji": ":red_circle:", "text": "ALARM 발생"},
    "OK": {"color": "#36A64F", "emoji": ":large_green_circle:", "text": "정상 복구"},
    "INSUFFICIENT_DATA": {"color": "#FFA500", "emoji": ":large_yellow_circle:", "text": "데이터 부족"},
}


def get_webhook_url() -> str:
    """Slack Webhook URL 조회 (환경 변수 또는 Secrets Manager)"""
    if SLACK_WEBHOOK_URL:
        return SLACK_WEBHOOK_URL

    if SLACK_SECRET_NAME:
        sm = boto3.client("secretsmanager")
        secret = sm.get_secret_value(SecretId=SLACK_SECRET_NAME)
        data = json.loads(secret["SecretString"])
        return data["webhook_url"]

    raise ValueError("SLACK_WEBHOOK_URL 또는 SLACK_SECRET_NAME 환경 변수 필요")


def parse_alarm_message(sns_message: str) -> dict:
    """SNS 메시지에서 CloudWatch Alarm 정보 파싱"""
    try:
        return json.loads(sns_message)
    except json.JSONDecodeError:
        return {"raw": sns_message}


def build_slack_payload(alarm: dict) -> dict:
    """Slack 메시지 페이로드 구성"""
    state = alarm.get("NewStateValue", "UNKNOWN")
    config = STATE_CONFIG.get(state, {"color": "#808080", "emoji": ":grey_question:", "text": state})

    alarm_name = alarm.get("AlarmName", "Unknown Alarm")
    description = alarm.get("AlarmDescription", "-")
    reason = alarm.get("NewStateReason", "-")
    region = alarm.get("Region", "-")
    account = alarm.get("AWSAccountId", "-")
    change_time = alarm.get("StateChangeTime", "-")
    old_state = alarm.get("OldStateValue", "-")

    # 트리거 정보
    trigger = alarm.get("Trigger", {})
    metric_name = trigger.get("MetricName", "-")
    namespace = trigger.get("Namespace", "-")
    threshold = trigger.get("Threshold", "-")

    # CloudWatch 콘솔 링크
    cw_url = (
        f"https://{region}.console.aws.amazon.com/cloudwatch/home"
        f"?region={region}#alarmsV2:alarm/{alarm_name}"
    )

    return {
        "attachments": [
            {
                "color": config["color"],
                "blocks": [
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": f"{config['emoji']} {config['text']}: {alarm_name}",
                        },
                    },
                    {
                        "type": "section",
                        "fields": [
                            {"type": "mrkdwn", "text": f"*상태 변경*\n{old_state} → `{state}`"},
                            {"type": "mrkdwn", "text": f"*리전 / 계정*\n{region} / {account}"},
                            {"type": "mrkdwn", "text": f"*메트릭*\n{namespace} / {metric_name}"},
                            {"type": "mrkdwn", "text": f"*임계값*\n{threshold}"},
                            {"type": "mrkdwn", "text": f"*설명*\n{description}"},
                            {"type": "mrkdwn", "text": f"*발생 시각*\n{change_time}"},
                        ],
                    },
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f"*원인*\n{reason}"},
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "CloudWatch 콘솔 열기"},
                                "url": cw_url,
                                "style": "primary" if state == "OK" else "danger",
                            }
                        ],
                    },
                ],
            }
        ]
    }


def send_slack(webhook_url: str, payload: dict) -> None:
    """Slack Webhook으로 메시지 전송"""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            logger.info("Slack 전송 완료: %s", resp.status)
    except urllib.error.HTTPError as e:
        logger.error("Slack 전송 실패: %s %s", e.code, e.read().decode())
        raise


def lambda_handler(event: dict, context) -> dict:
    logger.info("이벤트 수신: %s", json.dumps(event))

    webhook_url = get_webhook_url()
    processed = 0
    errors = []

    for record in event.get("Records", []):
        try:
            sns_message = record["Sns"]["Message"]
            alarm = parse_alarm_message(sns_message)

            payload = build_slack_payload(alarm)
            send_slack(webhook_url, payload)
            processed += 1

            logger.info("알람 처리 완료: %s → %s",
                       alarm.get("AlarmName"), alarm.get("NewStateValue"))

        except Exception as e:
            logger.error("처리 실패: %s", str(e))
            errors.append(str(e))

    return {
        "statusCode": 200 if not errors else 207,
        "processed": processed,
        "errors": errors,
    }
