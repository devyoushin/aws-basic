"""
AWS Cost Explorer 실무 boto3 쿼리 모음
사용법: python cost_explorer.py <명령> [인수]
"""

import boto3
import sys
import json
from datetime import datetime, timezone, timedelta, date
from typing import Optional

# Cost Explorer는 us-east-1 고정
session = boto3.Session()
ce = session.client("ce", region_name="us-east-1")


def _today() -> str:
    return date.today().isoformat()


def _first_of_month() -> str:
    today = date.today()
    return today.replace(day=1).isoformat()


def _last_month_range() -> tuple[str, str]:
    today = date.today()
    first_this_month = today.replace(day=1)
    last_month_end = first_this_month
    last_month_start = (first_this_month - timedelta(days=1)).replace(day=1)
    return last_month_start.isoformat(), last_month_end.isoformat()


# ─── 기간별 비용 조회 ─────────────────────────────────────────────────────────

def get_cost_by_service(
    start: str = None,
    end: str = None,
    granularity: str = "MONTHLY",
) -> list[dict]:
    """서비스별 비용 (내림차순)"""
    start = start or _first_of_month()
    end = end or _today()

    resp = ce.get_cost_and_usage(
        TimePeriod={"Start": start, "End": end},
        Granularity=granularity,
        Metrics=["BlendedCost", "UnblendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
    )

    results = []
    for period in resp["ResultsByTime"]:
        for group in period["Groups"]:
            cost = float(group["Metrics"]["BlendedCost"]["Amount"])
            if cost < 0.001:
                continue
            results.append({
                "period": period["TimePeriod"]["Start"],
                "service": group["Keys"][0],
                "blended_cost": round(cost, 4),
                "currency": group["Metrics"]["BlendedCost"]["Unit"],
            })

    return sorted(results, key=lambda x: x["blended_cost"], reverse=True)


def get_daily_cost(days: int = 30) -> list[dict]:
    """일별 총 비용 추이"""
    end = _today()
    start = (date.today() - timedelta(days=days)).isoformat()

    resp = ce.get_cost_and_usage(
        TimePeriod={"Start": start, "End": end},
        Granularity="DAILY",
        Metrics=["BlendedCost"],
    )

    return [
        {
            "date": period["TimePeriod"]["Start"],
            "cost": round(float(period["Total"]["BlendedCost"]["Amount"]), 4),
            "currency": period["Total"]["BlendedCost"]["Unit"],
        }
        for period in resp["ResultsByTime"]
    ]


def get_cost_by_tag(tag_key: str, start: str = None, end: str = None) -> list[dict]:
    """태그별 비용 분류 (예: Environment, Team, Project)"""
    start = start or _first_of_month()
    end = end or _today()

    resp = ce.get_cost_and_usage(
        TimePeriod={"Start": start, "End": end},
        Granularity="MONTHLY",
        Metrics=["BlendedCost"],
        GroupBy=[{"Type": "TAG", "Key": tag_key}],
    )

    results = []
    for period in resp["ResultsByTime"]:
        for group in period["Groups"]:
            results.append({
                "tag": group["Keys"][0] or "(태그 없음)",
                "cost": round(float(group["Metrics"]["BlendedCost"]["Amount"]), 4),
                "currency": group["Metrics"]["BlendedCost"]["Unit"],
            })

    return sorted(results, key=lambda x: x["cost"], reverse=True)


def get_cost_by_account(start: str = None, end: str = None) -> list[dict]:
    """연결 계정별 비용 (Organizations)"""
    start = start or _first_of_month()
    end = end or _today()

    resp = ce.get_cost_and_usage(
        TimePeriod={"Start": start, "End": end},
        Granularity="MONTHLY",
        Metrics=["BlendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "LINKED_ACCOUNT"}],
    )

    results = []
    for period in resp["ResultsByTime"]:
        for group in period["Groups"]:
            results.append({
                "account_id": group["Keys"][0],
                "cost": round(float(group["Metrics"]["BlendedCost"]["Amount"]), 4),
                "currency": group["Metrics"]["BlendedCost"]["Unit"],
            })

    return sorted(results, key=lambda x: x["cost"], reverse=True)


def get_cost_by_region(start: str = None, end: str = None) -> list[dict]:
    """리전별 비용"""
    start = start or _first_of_month()
    end = end or _today()

    resp = ce.get_cost_and_usage(
        TimePeriod={"Start": start, "End": end},
        Granularity="MONTHLY",
        Metrics=["BlendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "REGION"}],
    )

    results = []
    for period in resp["ResultsByTime"]:
        for group in period["Groups"]:
            cost = float(group["Metrics"]["BlendedCost"]["Amount"])
            if cost < 0.001:
                continue
            results.append({
                "region": group["Keys"][0] or "글로벌",
                "cost": round(cost, 4),
                "currency": group["Metrics"]["BlendedCost"]["Unit"],
            })

    return sorted(results, key=lambda x: x["cost"], reverse=True)


# ─── 비용 비교 / 급등 탐지 ───────────────────────────────────────────────────

def compare_month_over_month() -> dict:
    """이번 달 vs 지난달 서비스별 비용 비교"""
    last_start, last_end = _last_month_range()
    this_start = _first_of_month()
    this_end = _today()

    def fetch_costs(start, end):
        resp = ce.get_cost_and_usage(
            TimePeriod={"Start": start, "End": end},
            Granularity="MONTHLY",
            Metrics=["BlendedCost"],
            GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
        )
        return {
            g["Keys"][0]: round(float(g["Metrics"]["BlendedCost"]["Amount"]), 2)
            for g in resp["ResultsByTime"][0]["Groups"]
        }

    last_month = fetch_costs(last_start, last_end)
    this_month = fetch_costs(this_start, this_end)

    comparison = []
    all_services = set(list(last_month.keys()) + list(this_month.keys()))

    for svc in all_services:
        last = last_month.get(svc, 0)
        this = this_month.get(svc, 0)
        if last == 0 and this < 0.01:
            continue

        change_pct = ((this - last) / last * 100) if last > 0 else float("inf")
        comparison.append({
            "service": svc,
            "last_month": last,
            "this_month": this,
            "change": round(this - last, 2),
            "change_pct": f"{change_pct:+.1f}%" if change_pct != float("inf") else "신규",
        })

    return sorted(comparison, key=lambda x: abs(x["change"]), reverse=True)


def detect_cost_anomaly(threshold_pct: float = 50.0) -> list[dict]:
    """
    전일 대비 비용 급등 탐지
    threshold_pct: 이 이상 증가 시 알림 (기본 50%)
    """
    today = date.today()
    yesterday = (today - timedelta(days=1)).isoformat()
    day_before = (today - timedelta(days=2)).isoformat()
    today_str = today.isoformat()

    def fetch_daily(start, end):
        resp = ce.get_cost_and_usage(
            TimePeriod={"Start": start, "End": end},
            Granularity="DAILY",
            Metrics=["BlendedCost"],
            GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
        )
        if not resp["ResultsByTime"]:
            return {}
        return {
            g["Keys"][0]: float(g["Metrics"]["BlendedCost"]["Amount"])
            for g in resp["ResultsByTime"][0]["Groups"]
        }

    prev = fetch_daily(day_before, yesterday)
    curr = fetch_daily(yesterday, today_str)

    anomalies = []
    for svc, curr_cost in curr.items():
        prev_cost = prev.get(svc, 0)
        if prev_cost == 0:
            continue
        change_pct = (curr_cost - prev_cost) / prev_cost * 100
        if change_pct > threshold_pct:
            anomalies.append({
                "service": svc,
                "prev_day": round(prev_cost, 4),
                "curr_day": round(curr_cost, 4),
                "change_pct": f"+{change_pct:.1f}%",
            })

    return sorted(anomalies, key=lambda x: float(x["change_pct"].strip("+%")), reverse=True)


# ─── Savings Plans / RI ───────────────────────────────────────────────────────

def get_savings_plans_utilization(days: int = 30) -> dict:
    """Savings Plans 활용률"""
    end = _today()
    start = (date.today() - timedelta(days=days)).isoformat()

    resp = ce.get_savings_plans_utilization(
        TimePeriod={"Start": start, "End": end},
    )
    total = resp["Total"]

    return {
        "total_commitment": total["TotalCommitment"],
        "used_commitment": total["UsedCommitment"],
        "unused_commitment": total["UnusedCommitment"],
        "utilization_pct": total["UtilizationPercentage"],
        "net_savings": total.get("NetSavings", "-"),
    }


def get_ri_utilization(days: int = 30) -> list[dict]:
    """Reserved Instance 활용률"""
    end = _today()
    start = (date.today() - timedelta(days=days)).isoformat()

    resp = ce.get_reservation_utilization(
        TimePeriod={"Start": start, "End": end},
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
    )

    results = []
    for group in resp.get("UtilizationsByTime", [{}])[0].get("Groups", []):
        util = group["Utilization"]
        results.append({
            "service": group["Keys"][0],
            "utilization_pct": util.get("UtilizationPercentage", "-"),
            "purchased_hours": util.get("PurchasedHours", "-"),
            "used_hours": util.get("UsedHours", "-"),
            "unused_hours": util.get("UnusedHours", "-"),
        })

    return results


# ─── 비용 예측 ────────────────────────────────────────────────────────────────

def forecast_monthly_cost() -> dict:
    """이번 달 말까지 비용 예측"""
    today = date.today()
    end_of_month = today.replace(day=1).replace(month=today.month % 12 + 1)
    if today.month == 12:
        end_of_month = date(today.year + 1, 1, 1)

    resp = ce.get_cost_forecast(
        TimePeriod={"Start": _today(), "End": end_of_month.isoformat()},
        Metric="BLENDED_COST",
        Granularity="MONTHLY",
    )

    forecast = resp["Total"]
    return {
        "forecast_start": _today(),
        "forecast_end": end_of_month.isoformat(),
        "mean_value": round(float(forecast["Amount"]), 2),
        "unit": forecast["Unit"],
        "prediction_interval_lower": round(
            float(resp.get("ForecastResultsByTime", [{}])[0].get("PredictionIntervalLowerBound", "0")), 2
        ) if resp.get("ForecastResultsByTime") else "-",
        "prediction_interval_upper": round(
            float(resp.get("ForecastResultsByTime", [{}])[0].get("PredictionIntervalUpperBound", "0")), 2
        ) if resp.get("ForecastResultsByTime") else "-",
    }


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

    if cmd == "by-service":
        start = sys.argv[2] if len(sys.argv) > 2 else None
        end = sys.argv[3] if len(sys.argv) > 3 else None
        print_table(get_cost_by_service(start, end))
    elif cmd == "daily":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        print_table(get_daily_cost(days))
    elif cmd == "by-tag":
        print_table(get_cost_by_tag(sys.argv[2]))
    elif cmd == "by-account":
        print_table(get_cost_by_account())
    elif cmd == "by-region":
        print_table(get_cost_by_region())
    elif cmd == "mom":
        print_table(compare_month_over_month())
    elif cmd == "anomaly":
        threshold = float(sys.argv[2]) if len(sys.argv) > 2 else 50.0
        print_table(detect_cost_anomaly(threshold))
    elif cmd == "sp-util":
        print(json.dumps(get_savings_plans_utilization(), indent=2))
    elif cmd == "ri-util":
        print_table(get_ri_utilization())
    elif cmd == "forecast":
        print(json.dumps(forecast_monthly_cost(), indent=2))
    else:
        print("사용법: python cost_explorer.py <명령> [인수]\n")
        print("  by-service [START END]   서비스별 비용")
        print("  daily [DAYS]             일별 비용 추이")
        print("  by-tag TAG_KEY           태그별 비용")
        print("  by-account               계정별 비용")
        print("  by-region                리전별 비용")
        print("  mom                      전월 대비 비교")
        print("  anomaly [THRESHOLD%]     비용 급등 탐지")
        print("  sp-util                  Savings Plans 활용률")
        print("  ri-util                  RI 활용률")
        print("  forecast                 월말 비용 예측")
