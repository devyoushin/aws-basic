"""
EKS 실무 boto3 쿼리 모음
사용법: python eks_queries.py <명령> [인수]
"""

import boto3
import sys
import json
from datetime import datetime, timezone
from typing import Optional

session = boto3.Session(region_name="ap-northeast-2")
eks = session.client("eks")
iam = session.client("iam")
ec2 = session.client("ec2")


# ─── 클러스터 조회 ────────────────────────────────────────────────────────────

def list_clusters() -> list[str]:
    """EKS 클러스터 목록"""
    paginator = eks.get_paginator("list_clusters")
    clusters = []
    for page in paginator.paginate():
        clusters.extend(page["clusters"])
    return clusters


def describe_cluster(cluster_name: str) -> dict:
    """클러스터 상세 정보"""
    resp = eks.describe_cluster(name=cluster_name)
    cluster = resp["cluster"]

    return {
        "name": cluster["name"],
        "version": cluster["version"],
        "status": cluster["status"],
        "endpoint": cluster.get("endpoint", "-"),
        "oidc_issuer": cluster.get("identity", {}).get("oidc", {}).get("issuer", "-"),
        "role_arn": cluster.get("roleArn", "-"),
        "vpc_id": cluster.get("resourcesVpcConfig", {}).get("vpcId", "-"),
        "created_at": cluster["createdAt"].isoformat(),
        "kubernetes_network_config": cluster.get("kubernetesNetworkConfig", {}),
        "logging": cluster.get("logging", {}).get("clusterLogging", []),
    }


def get_all_clusters_summary() -> list[dict]:
    """전체 클러스터 요약"""
    clusters = list_clusters()
    results = []

    for name in clusters:
        cluster = eks.describe_cluster(name=name)["cluster"]
        results.append({
            "name": name,
            "version": cluster["version"],
            "status": cluster["status"],
            "created_at": cluster["createdAt"].strftime("%Y-%m-%d"),
        })

    return results


# ─── 노드 그룹 ────────────────────────────────────────────────────────────────

def list_nodegroups(cluster_name: str) -> list[str]:
    """노드 그룹 목록"""
    paginator = eks.get_paginator("list_nodegroups")
    nodegroups = []
    for page in paginator.paginate(clusterName=cluster_name):
        nodegroups.extend(page["nodegroups"])
    return nodegroups


def describe_nodegroup(cluster_name: str, nodegroup_name: str) -> dict:
    """노드 그룹 상세"""
    resp = eks.describe_nodegroup(clusterName=cluster_name, nodegroupName=nodegroup_name)
    ng = resp["nodegroup"]

    return {
        "name": ng["nodegroupName"],
        "status": ng["status"],
        "instance_types": ng.get("instanceTypes", []),
        "ami_type": ng.get("amiType", "-"),
        "release_version": ng.get("releaseVersion", "-"),
        "min_size": ng["scalingConfig"]["minSize"],
        "max_size": ng["scalingConfig"]["maxSize"],
        "desired_size": ng["scalingConfig"]["desiredSize"],
        "disk_size": ng.get("diskSize", "-"),
        "subnets": ng.get("subnets", []),
        "node_role": ng.get("nodeRole", "-"),
        "labels": ng.get("labels", {}),
        "taints": ng.get("taints", []),
        "created_at": ng["createdAt"].isoformat(),
        "modified_at": ng["modifiedAt"].isoformat(),
    }


def get_all_nodegroups_capacity(cluster_name: str) -> list[dict]:
    """클러스터 내 모든 노드 그룹 용량 현황"""
    nodegroups = list_nodegroups(cluster_name)
    results = []

    for ng_name in nodegroups:
        ng = eks.describe_nodegroup(clusterName=cluster_name, nodegroupName=ng_name)["nodegroup"]
        scaling = ng["scalingConfig"]
        results.append({
            "nodegroup": ng_name,
            "status": ng["status"],
            "instance_types": ", ".join(ng.get("instanceTypes", [])),
            "min": scaling["minSize"],
            "max": scaling["maxSize"],
            "desired": scaling["desiredSize"],
            "ami_type": ng.get("amiType", "-"),
        })

    return results


# ─── 애드온 ───────────────────────────────────────────────────────────────────

def list_addons(cluster_name: str) -> list[dict]:
    """클러스터 애드온 목록 및 버전"""
    paginator = eks.get_paginator("list_addons")
    addon_names = []
    for page in paginator.paginate(clusterName=cluster_name):
        addon_names.extend(page["addons"])

    results = []
    for name in addon_names:
        addon = eks.describe_addon(clusterName=cluster_name, addonName=name)["addon"]
        results.append({
            "name": name,
            "version": addon["addonVersion"],
            "status": addon["status"],
            "service_account_role": addon.get("serviceAccountRoleArn", "-"),
            "created": addon["createdAt"].strftime("%Y-%m-%d"),
        })

    return results


def check_addon_updates(cluster_name: str) -> list[dict]:
    """애드온 업데이트 가능 여부 확인"""
    cluster = eks.describe_cluster(name=cluster_name)["cluster"]
    k8s_version = cluster["version"]

    addons = list_addons(cluster_name)
    results = []

    for addon in addons:
        # 최신 버전 조회
        versions_resp = eks.describe_addon_versions(
            kubernetesVersion=k8s_version,
            addonName=addon["name"],
        )
        latest_version = "-"
        if versions_resp["addons"] and versions_resp["addons"][0]["addonVersions"]:
            latest_version = versions_resp["addons"][0]["addonVersions"][0]["addonVersion"]

        results.append({
            "addon": addon["name"],
            "current": addon["version"],
            "latest": latest_version,
            "update_available": addon["version"] != latest_version,
        })

    return results


# ─── IRSA ─────────────────────────────────────────────────────────────────────

def get_oidc_issuer(cluster_name: str) -> str:
    """OIDC Issuer URL"""
    cluster = eks.describe_cluster(name=cluster_name)["cluster"]
    return cluster.get("identity", {}).get("oidc", {}).get("issuer", "")


def check_oidc_provider_exists(cluster_name: str) -> dict:
    """OIDC Provider 등록 여부 확인 (IRSA 전제 조건)"""
    issuer = get_oidc_issuer(cluster_name)
    if not issuer:
        return {"registered": False, "issuer": "-"}

    # arn:aws:iam::ACCOUNT:oidc-provider/oidc.eks.REGION.amazonaws.com/...
    account_id = boto3.client("sts").get_caller_identity()["Account"]
    issuer_host = issuer.replace("https://", "")
    oidc_arn = f"arn:aws:iam::{account_id}:oidc-provider/{issuer_host}"

    try:
        iam.get_open_id_connect_provider(OpenIDConnectProviderArn=oidc_arn)
        return {"registered": True, "issuer": issuer, "arn": oidc_arn}
    except iam.exceptions.NoSuchEntityException:
        return {"registered": False, "issuer": issuer, "arn": oidc_arn}


def generate_irsa_trust_policy(cluster_name: str, namespace: str, service_account: str) -> dict:
    """
    IRSA용 Trust Policy 생성
    이 문서를 IAM Role의 Trust Policy로 설정하면 됨
    """
    account_id = boto3.client("sts").get_caller_identity()["Account"]
    region = session.region_name
    issuer = get_oidc_issuer(cluster_name).replace("https://", "")

    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {
                    "Federated": f"arn:aws:iam::{account_id}:oidc-provider/{issuer}"
                },
                "Action": "sts:AssumeRoleWithWebIdentity",
                "Condition": {
                    "StringEquals": {
                        f"{issuer}:sub": f"system:serviceaccount:{namespace}:{service_account}",
                        f"{issuer}:aud": "sts.amazonaws.com",
                    }
                },
            }
        ],
    }


# ─── 업그레이드 분석 ──────────────────────────────────────────────────────────

def analyze_upgrade_readiness(cluster_name: str, target_version: str) -> dict:
    """클러스터 업그레이드 준비 상태 분석"""
    cluster = describe_cluster(cluster_name)
    current_version = cluster["version"]

    addons = list_addons(cluster_name)
    nodegroups = get_all_nodegroups_capacity(cluster_name)

    # 노드 그룹 버전 확인
    ng_versions = []
    for ng in nodegroups:
        ng_detail = eks.describe_nodegroup(
            clusterName=cluster_name,
            nodegroupName=ng["nodegroup"]
        )["nodegroup"]
        ng_versions.append({
            "nodegroup": ng["nodegroup"],
            "release_version": ng_detail.get("releaseVersion", "-"),
            "needs_update": True,  # 업그레이드 후 노드 그룹도 업데이트 필요
        })

    return {
        "cluster": cluster_name,
        "current_version": current_version,
        "target_version": target_version,
        "version_diff": f"{current_version} → {target_version}",
        "addon_count": len(addons),
        "nodegroup_count": len(nodegroups),
        "nodegroups_to_update": ng_versions,
        "checklist": [
            "✅ kubectl 버전 확인 (target±1 버전)",
            "✅ deprecated API 탐지 (pluto, kubent 도구 사용)",
            "✅ 애드온 호환 버전 확인",
            "✅ PDB 설정 확인",
            "✅ 노드 드레인 순서 계획",
            "⚠️  업그레이드는 한 번에 마이너 버전 1단계씩만 가능",
        ],
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

    if cmd == "clusters":
        print_table(get_all_clusters_summary())
    elif cmd == "describe":
        print(json.dumps(describe_cluster(sys.argv[2]), indent=2, default=str, ensure_ascii=False))
    elif cmd == "ng-list":
        print_table(get_all_nodegroups_capacity(sys.argv[2]))
    elif cmd == "ng-detail":
        print(json.dumps(describe_nodegroup(sys.argv[2], sys.argv[3]), indent=2, default=str, ensure_ascii=False))
    elif cmd == "addons":
        print_table(list_addons(sys.argv[2]))
    elif cmd == "addon-updates":
        print_table(check_addon_updates(sys.argv[2]))
    elif cmd == "oidc":
        print(get_oidc_issuer(sys.argv[2]))
    elif cmd == "oidc-check":
        print(json.dumps(check_oidc_provider_exists(sys.argv[2]), indent=2))
    elif cmd == "irsa-trust":
        # python eks_queries.py irsa-trust CLUSTER NAMESPACE SERVICE_ACCOUNT
        policy = generate_irsa_trust_policy(sys.argv[2], sys.argv[3], sys.argv[4])
        print(json.dumps(policy, indent=2))
    elif cmd == "upgrade-check":
        result = analyze_upgrade_readiness(sys.argv[2], sys.argv[3])
        print(json.dumps(result, indent=2, default=str, ensure_ascii=False))
    else:
        print("사용법: python eks_queries.py <명령> [인수]\n")
        print("  clusters                  클러스터 목록")
        print("  describe CLUSTER          클러스터 상세")
        print("  ng-list CLUSTER           노드 그룹 용량 현황")
        print("  ng-detail CLUSTER NG      노드 그룹 상세")
        print("  addons CLUSTER            애드온 목록")
        print("  addon-updates CLUSTER     애드온 업데이트 확인")
        print("  oidc CLUSTER              OIDC Issuer URL")
        print("  oidc-check CLUSTER        OIDC Provider 등록 여부")
        print("  irsa-trust CLUSTER NS SA  IRSA Trust Policy 생성")
        print("  upgrade-check CLUSTER VER 업그레이드 준비 분석")
