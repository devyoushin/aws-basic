"""
AWS 비용 이상 탐지 및 Slack/SNS 알림
매일 전일 비용을 분석하여 급등 서비스를 감지하고 알림을 발송합니다.

트리거: EventBridge Scheduler
  cron(0 9 * * ? *)  → 매일 오전 9시 (KST 기준 UTC 0시)

필요 IAM 권한:
  - ce:GetCostAndUsage         (Cost Explorer)
  - ce:GetCostForecast         (비용 예측)
  - sns:Publish                (SNS 알림)
  - secretsmanager:GetSecretValue  (Slack Webhook)

환경 변수:
  - THRESHOLD_PCT: 급등 기준 (기본: 50 → 전일 대비 50% 이상 증가 시 알림)
  - MIN_COST_USD: 무시할 최소 비용 (기본: 1.0 → $1 미만은 알림 제외)
  - SLACK_WEBHOOK_URL: Slack Webhook URL
  - SNS_TOPIC_ARN: SNS 토픽 ARN (선택)
  - CURRENCY: 통화 (기본: USD)
"""

import boto3
import json
import logging
import os
import urllib.request
from datetime import date, timedelta

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ce = boto3.client("ce", region_name="us-east-1")
sns_client = boto3.client("sns")

THRESHOLD_PCT = float(os.environ.get("THRESHOLD_PCT", "50"))
MIN_COST_USD = float(os.environ.get("MIN_COST_USD", "1.0"))
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN", "")


# ─── 비용 조회 ────────────────────────────────────────────────────────────────

def get_daily_cost_by_service(start: str, end: str) -> dict[str, float]:
    """특정 날짜의 서비스별 비용 조회"""
    resp = ce.get_cost_and_usage(
        TimePeriod={"Start": start, "End": end},
        Granularity="DAILY",
        Metrics=["BlendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
    )

    if not resp["ResultsByTime"]:
        return {}

    return {
        group["Keys"][0]: float(group["Metrics"]["BlendedCost"]["Amount"])
        for group in resp["ResultsByTime"][0]["Groups"]
    }


def get_monthly_forecast() -> dict:
    """이번 달 말까지 비용 예측"""
    today = date.today()
    # 이번 달 말일 다음날
    if today.month == 12:
        end_of_month = date(today.year + 1, 1, 1)
    else:
        end_of_month = date(today.year, today.month + 1, 1)

    tomorrow = (today + timedelta(days=1)).isoformat()

    try:
        resp = ce.get_cost_forecast(
            TimePeriod={"Start": tomorrow, "End": end_of_month.isoformat()},
            Metric="BLENDED_COST",
            Granularity="MONTHLY",
        )
        return {
            "forecast_amount": round(float(resp["Total"]["Amount"]), 2),
            "unit": resp["Total"]["Unit"],
        }
    except Exception as e:
        logger.warning("예측 실패: %s", e)
        return {}


def get_this_month_total() -> float:
    """이번 달 현재까지 총 비용"""
    today = date.today()
    first_of_month = today.replace(day=1).isoformat()

    resp = ce.get_cost_and_usage(
        TimePeriod={"Start": first_of_month, "End": today.isoformat()},
        Granularity="MONTHLY",
        Metrics=["BlendedCost"],
    )

    if not resp["ResultsByTime"]:
        return 0.0

    return float(resp["ResultsByTime"][0]["Total"]["BlendedCost"]["Amount"])


# ─── 분석 ─────────────────────────────────────────────────────────────────────

def detect_anomalies(yesterday_costs: dict, day_before_costs: dict) -> list[dict]:
    """
    전일 vs 그 전날 비용 비교하여 급등 서비스 탐지
    - 증가율 > THRESHOLD_PCT
    - 금액 > MIN_COST_USD
    """
    anomalies = []

    for service, curr_cost in yesterday_costs.items():
        if curr_cost < MIN_COST_USD:
            continue

        prev_cost = day_before_costs.get(service, 0)

        if prev_cost == 0:
            # 신규 서비스 (전날에 없었던 서비스)
            if curr_cost >= MIN_COST_USD * 5:
                anomalies.append({
                    "service": service,
                    "prev_cost": 0.0,
                    "curr_cost": round(curr_cost, 4),
                    "change_pct": None,
                    "change_type": "신규",
                })
            continue

        change_pct = (curr_cost - prev_cost) / prev_cost * 100

        if change_pct >= THRESHOLD_PCT:
            anomalies.append({
                "service": service,
                "prev_cost": round(prev_cost, 4),
                "curr_cost": round(curr_cost, 4),
                "change_pct": round(change_pct, 1),
                "change_type": "급등",
            })

    return sorted(anomalies, key=lambda x: x["curr_cost"], reverse=True)


# ─── 알림 발송 ────────────────────────────────────────────────────────────────

def build_slack_message(report: dict) -> dict:
    """Slack 메시지 구성"""
    yesterday = report["date"]["yesterday"]
    anomalies = report["anomalies"]
    total_yesterday = report["totals"]["yesterday"]
    total_day_before = report["totals"]["day_before"]
    monthly_total = report["totals"]["this_month"]
    forecast = report.get("forecast", {})

    change_pct = (
        (total_yesterday - total_day_before) / total_day_before * 100
        if total_day_before > 0 else 0
    )

    # 전체 변화에 따른 색상
    color = "#FF0000" if change_pct > THRESHOLD_PCT else "#FFA500" if change_pct > 20 else "#36A64F"
    header_emoji = ":rotating_light:" if anomalies else ":white_check_mark:"

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{header_emoji} AWS 일일 비용 리포트 ({yesterday})",
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*어제 총 비용*\n${total_yesterday:.2f}"},
                {"type": "mrkdwn", "text": f"*전일 대비*\n{change_pct:+.1f}% (${total_yesterday - total_day_before:+.2f})"},
                {"type": "mrkdwn", "text": f"*이번 달 누적*\n${monthly_total:.2f}"},
                {"type": "mrkdwn",
                 "text": f"*월말 예측*\n${forecast.get('forecast_amount', '?')}"},
            ],
        },
    ]

    if anomalies:
        anomaly_text = f"*:warning: 비용 급등 서비스 ({len(anomalies)}개)*\n"
        for a in anomalies[:5]:
            if a["change_pct"] is not None:
                anomaly_text += f"• {a['service']}: ${a['prev_cost']} → ${a['curr_cost']} ({a['change_pct']:+.1f}%)\n"
            else:
                anomaly_text += f"• {a['service']}: 신규 발생 ${a['curr_cost']}\n"

        if len(anomalies) > 5:
            anomaly_text += f"  _외 {len(anomalies) - 5}개..._"

        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": anomaly_text},
        })

    return {
        "attachments": [{"color": color, "blocks": blocks}]
    }


def send_slack(payload: dict) -> None:
    if not SLACK_WEBHOOK_URL:
        return

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        SLACK_WEBHOOK_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        logger.info("Slack 전송: %s", resp.status)


def send_sns(report: dict) -> None:
    if not SNS_TOPIC_ARN:
        return

    anomalies = report["anomalies"]
    subject = (
        f"[AWS 비용 급등] {len(anomalies)}개 서비스 이상 탐지"
        if anomalies
        else f"[AWS 비용 정상] 어제 총 ${report['totals']['yesterday']:.2f}"
    )

    sns_client.publish(
        TopicArn=SNS_TOPIC_ARN,
        Subject=subject,
        Message=json.dumps(report, ensure_ascii=False, default=str),
    )
    logger.info("SNS 전송 완료")


# ─── 핸들러 ───────────────────────────────────────────────────────────────────

def lambda_handler(event: dict, context) -> dict:
    today = date.today()
    yesterday = (today - timedelta(days=1)).isoformat()
    day_before = (today - timedelta(days=2)).isoformat()

    logger.info("비용 분석: %s vs %s", yesterday, day_before)

    # 비용 조회
    yesterday_costs = get_daily_cost_by_service(yesterday, today.isoformat())
    day_before_costs = get_daily_cost_by_service(day_before, yesterday)

    total_yesterday = sum(yesterday_costs.values())
    total_day_before = sum(day_before_costs.values())

    # 이상 탐지
    anomalies = detect_anomalies(yesterday_costs, day_before_costs)

    # 예측 및 누적
    monthly_total = get_this_month_total()
    forecast = get_monthly_forecast()

    report = {
        "date": {"yesterday": yesterday, "day_before": day_before},
        "totals": {
            "yesterday": round(total_yesterday, 4),
            "day_before": round(total_day_before, 4),
            "this_month": round(monthly_total, 2),
        },
        "forecast": forecast,
        "anomaly_count": len(anomalies),
        "anomalies": anomalies,
        "threshold_pct": THRESHOLD_PCT,
    }

    logger.info("분석 완료: 이상 %d개", len(anomalies))

    # 알림 발송
    slack_payload = build_slack_message(report)
    send_slack(slack_payload)
    send_sns(report)

    return {"statusCode": 200, **report}
