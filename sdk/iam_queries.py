"""
IAM 실무 boto3 쿼리 모음 — 권한 감사, 자격 증명 관리
사용법: python iam_queries.py <명령> [인수]
"""

import boto3
import sys
import json
from datetime import datetime, timezone, timedelta
from typing import Optional

session = boto3.Session()
iam = session.client("iam")
sts = session.client("sts")


# ─── 자격 증명 확인 ────────────────────────────────────────────────────────────

def whoami() -> dict:
    """현재 자격 증명 정보"""
    return sts.get_caller_identity()


# ─── 사용자 관리 ──────────────────────────────────────────────────────────────

def list_users() -> list[dict]:
    """IAM 사용자 목록"""
    paginator = iam.get_paginator("list_users")
    users = []

    for page in paginator.paginate():
        for user in page["Users"]:
            users.append({
                "username": user["UserName"],
                "user_id": user["UserId"],
                "created": user["CreateDate"].strftime("%Y-%m-%d"),
                "last_used": user.get("PasswordLastUsed", datetime(1970, 1, 1, tzinfo=timezone.utc)).strftime("%Y-%m-%d"),
            })

    return sorted(users, key=lambda x: x["last_used"], reverse=True)


def get_access_key_info() -> list[dict]:
    """전체 사용자 액세스 키 현황"""
    paginator = iam.get_paginator("list_users")
    results = []

    for page in paginator.paginate():
        for user in page["Users"]:
            keys_resp = iam.list_access_keys(UserName=user["UserName"])
            for key in keys_resp["AccessKeyMetadata"]:
                # 마지막 사용 날짜
                try:
                    last_used_resp = iam.get_access_key_last_used(AccessKeyId=key["AccessKeyId"])
                    last_used = last_used_resp["AccessKeyLastUsed"].get("LastUsedDate")
                    last_used_str = last_used.strftime("%Y-%m-%d") if last_used else "미사용"
                    service = last_used_resp["AccessKeyLastUsed"].get("ServiceName", "-")
                except Exception:
                    last_used_str = "unknown"
                    service = "-"

                created = key["CreateDate"]
                age_days = (datetime.now(timezone.utc) - created).days

                results.append({
                    "username": user["UserName"],
                    "access_key_id": key["AccessKeyId"],
                    "status": key["Status"],
                    "created": created.strftime("%Y-%m-%d"),
                    "age_days": age_days,
                    "last_used": last_used_str,
                    "last_service": service,
                    "⚠️": "교체 필요" if age_days > 90 else "",
                })

    return results


def find_users_without_mfa() -> list[dict]:
    """콘솔 접근 가능하지만 MFA 미설정 사용자"""
    paginator = iam.get_paginator("list_users")
    at_risk = []

    for page in paginator.paginate():
        for user in page["Users"]:
            # 콘솔 로그인 프로파일 확인
            try:
                iam.get_login_profile(UserName=user["UserName"])
                has_console = True
            except iam.exceptions.NoSuchEntityException:
                has_console = False

            if not has_console:
                continue

            # MFA 기기 확인
            mfa_resp = iam.list_mfa_devices(UserName=user["UserName"])
            if not mfa_resp["MFADevices"]:
                at_risk.append({
                    "username": user["UserName"],
                    "created": user["CreateDate"].strftime("%Y-%m-%d"),
                    "last_login": user.get("PasswordLastUsed", "없음"),
                    "risk": "콘솔 접근 가능, MFA 없음",
                })

    return at_risk


# ─── 역할 관리 ────────────────────────────────────────────────────────────────

def list_roles(prefix: str = "") -> list[dict]:
    """IAM 역할 목록"""
    paginator = iam.get_paginator("list_roles")
    roles = []

    kwargs = {}
    if prefix:
        kwargs["PathPrefix"] = f"/"

    for page in paginator.paginate(**kwargs):
        for role in page["Roles"]:
            if prefix and prefix.lower() not in role["RoleName"].lower():
                continue
            roles.append({
                "name": role["RoleName"],
                "role_id": role["RoleId"],
                "created": role["CreateDate"].strftime("%Y-%m-%d"),
                "path": role["Path"],
            })

    return roles


def get_role_detail(role_name: str) -> dict:
    """역할 상세 (Trust Policy + 연결 정책)"""
    resp = iam.get_role(RoleName=role_name)
    role = resp["Role"]

    # 연결된 관리형 정책
    attached = iam.list_attached_role_policies(RoleName=role_name)["AttachedPolicies"]

    # 인라인 정책
    inline = iam.list_role_policies(RoleName=role_name)["PolicyNames"]

    return {
        "name": role["RoleName"],
        "arn": role["Arn"],
        "created": role["CreateDate"].isoformat(),
        "trust_policy": role["AssumeRolePolicyDocument"],
        "attached_policies": [p["PolicyName"] for p in attached],
        "inline_policies": inline,
        "max_session_duration": role.get("MaxSessionDuration", 3600),
    }


def find_irsa_roles() -> list[dict]:
    """OIDC Trust가 포함된 역할 (EKS IRSA)"""
    paginator = iam.get_paginator("list_roles")
    irsa_roles = []

    for page in paginator.paginate():
        for role in page["Roles"]:
            trust = role.get("AssumeRolePolicyDocument", {})
            for stmt in trust.get("Statement", []):
                principal = stmt.get("Principal", {})
                federated = principal.get("Federated", "")
                if "oidc" in str(federated).lower():
                    # 서비스 어카운트 어노테이션 조건 추출
                    condition = stmt.get("Condition", {})
                    sa_name = "-"
                    for _, cond_vals in condition.items():
                        for k, v in cond_vals.items():
                            if "sub" in k.lower():
                                sa_name = str(v)

                    irsa_roles.append({
                        "role_name": role["RoleName"],
                        "oidc_provider": str(federated).split("/")[-1][:40],
                        "service_account": sa_name,
                    })
                    break

    return irsa_roles


# ─── 정책 관리 ────────────────────────────────────────────────────────────────

def list_customer_managed_policies() -> list[dict]:
    """고객 관리형 정책 목록"""
    paginator = iam.get_paginator("list_policies")
    policies = []

    for page in paginator.paginate(Scope="Local"):
        for policy in page["Policies"]:
            policies.append({
                "name": policy["PolicyName"],
                "arn": policy["Arn"],
                "attachment_count": policy["AttachmentCount"],
                "created": policy["CreateDate"].strftime("%Y-%m-%d"),
                "updated": policy["UpdatedDate"].strftime("%Y-%m-%d"),
            })

    return sorted(policies, key=lambda x: x["attachment_count"], reverse=True)


def get_policy_document(policy_arn: str) -> dict:
    """정책 문서 내용 확인"""
    policy = iam.get_policy(PolicyArn=policy_arn)["Policy"]
    version_id = policy["DefaultVersionId"]

    version = iam.get_policy_version(PolicyArn=policy_arn, VersionId=version_id)
    return version["PolicyVersion"]["Document"]


def find_unattached_policies() -> list[dict]:
    """어디에도 연결되지 않은 정책 (정리 대상)"""
    paginator = iam.get_paginator("list_policies")
    unused = []

    for page in paginator.paginate(Scope="Local"):
        for policy in page["Policies"]:
            if policy["AttachmentCount"] == 0:
                unused.append({
                    "name": policy["PolicyName"],
                    "arn": policy["Arn"],
                    "created": policy["CreateDate"].strftime("%Y-%m-%d"),
                })

    return unused


# ─── 권한 시뮬레이션 ──────────────────────────────────────────────────────────

def simulate_permission(
    principal_arn: str,
    actions: list[str],
    resources: list[str] = None,
) -> list[dict]:
    """
    특정 주체가 특정 액션을 수행할 수 있는지 시뮬레이션
    principal_arn: IAM Role ARN 또는 User ARN
    actions 예시: ["s3:GetObject", "ec2:DescribeInstances"]
    """
    if resources is None:
        resources = ["*"]

    resp = iam.simulate_principal_policy(
        PolicySourceArn=principal_arn,
        ActionNames=actions,
        ResourceArns=resources,
    )

    return [
        {
            "action": result["EvalActionName"],
            "decision": result["EvalDecision"],
            "resource": result["EvalResourceName"],
            "matched_policies": [
                s.get("MatchedStatements", [{}])[0].get("SourcePolicyId", "-")
                for s in result.get("MatchedStatements", [])
            ],
        }
        for result in resp["EvaluationResults"]
    ]


# ─── Assume Role ──────────────────────────────────────────────────────────────

def assume_role(role_arn: str, session_name: str = "AssumedSession", duration: int = 3600) -> dict:
    """
    역할 위임 (Cross-account, 임시 자격 증명 획득)
    반환된 자격 증명으로 boto3.Session 생성 가능
    """
    resp = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName=session_name,
        DurationSeconds=duration,
    )
    creds = resp["Credentials"]

    print(f"[Assume Role 성공]")
    print(f"만료: {creds['Expiration'].isoformat()}")

    # 환경 변수로 내보내기 위한 export 명령 출력
    print("\n# 다음 명령으로 환경 변수 설정:")
    print(f"export AWS_ACCESS_KEY_ID={creds['AccessKeyId']}")
    print(f"export AWS_SECRET_ACCESS_KEY={creds['SecretAccessKey']}")
    print(f"export AWS_SESSION_TOKEN={creds['SessionToken']}")

    return {
        "access_key_id": creds["AccessKeyId"],
        "secret_access_key": creds["SecretAccessKey"],
        "session_token": creds["SessionToken"],
        "expiration": creds["Expiration"].isoformat(),
    }


def get_assumed_session(role_arn: str, session_name: str = "BotoSession") -> boto3.Session:
    """Assume Role 후 새 Session 반환 (다른 스크립트에서 import해서 사용)"""
    creds = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName=session_name,
        DurationSeconds=3600,
    )["Credentials"]

    return boto3.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
    )


# ─── 자격 증명 리포트 ────────────────────────────────────────────────────────

def get_credential_report() -> list[dict]:
    """전체 사용자 자격 증명 리포트 (CSV 파싱)"""
    import base64
    import csv
    import io

    # 리포트 생성 요청
    while True:
        resp = iam.generate_credential_report()
        if resp["State"] == "COMPLETE":
            break

    report = iam.get_credential_report()
    content = base64.b64decode(report["Content"]).decode("utf-8")

    reader = csv.DictReader(io.StringIO(content))
    return list(reader)


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

    if cmd == "whoami":
        print(json.dumps(whoami(), indent=2, default=str))
    elif cmd == "users":
        print_table(list_users())
    elif cmd == "key-info":
        print_table(get_access_key_info())
    elif cmd == "no-mfa":
        print_table(find_users_without_mfa())
    elif cmd == "roles":
        prefix = sys.argv[2] if len(sys.argv) > 2 else ""
        print_table(list_roles(prefix))
    elif cmd == "role-detail":
        result = get_role_detail(sys.argv[2])
        print(json.dumps(result, indent=2, default=str, ensure_ascii=False))
    elif cmd == "irsa":
        print_table(find_irsa_roles())
    elif cmd == "policies":
        print_table(list_customer_managed_policies())
    elif cmd == "policy-doc":
        result = get_policy_document(sys.argv[2])
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif cmd == "unattached":
        print_table(find_unattached_policies())
    elif cmd == "simulate":
        # python iam_queries.py simulate ARN action1,action2
        actions = sys.argv[3].split(",") if len(sys.argv) > 3 else []
        print_table(simulate_permission(sys.argv[2], actions))
    elif cmd == "assume":
        assume_role(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "BotoSession")
    elif cmd == "cred-report":
        report = get_credential_report()
        # CSV 헤더 출력
        if report:
            print(",".join(report[0].keys()))
            for row in report:
                print(",".join(str(v) for v in row.values()))
    else:
        print("사용법: python iam_queries.py <명령> [인수]\n")
        print("  whoami                  현재 자격 증명")
        print("  users                   사용자 목록")
        print("  key-info                액세스 키 현황")
        print("  no-mfa                  MFA 미설정 사용자")
        print("  roles [PREFIX]          역할 목록")
        print("  role-detail ROLE_NAME   역할 상세")
        print("  irsa                    IRSA 역할 목록")
        print("  policies                커스텀 정책 목록")
        print("  policy-doc ARN          정책 문서")
        print("  unattached              미사용 정책")
        print("  simulate ARN ACTIONS    권한 시뮬레이션")
        print("  assume ROLE_ARN [NAME]  역할 위임")
        print("  cred-report             자격 증명 리포트")
