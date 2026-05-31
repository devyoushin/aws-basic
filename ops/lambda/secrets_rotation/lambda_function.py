"""
Secrets Manager 자격 증명 자동 교체 (Custom Rotation Lambda)
RDS 데이터베이스 암호를 자동으로 교체하는 Lambda입니다.
Secrets Manager의 Rotation 기능과 연동됩니다.

트리거: Secrets Manager Rotation (자동 호출)
  Secrets Manager → Lambda (4단계 교체 프로세스)

필요 IAM 권한:
  - secretsmanager:GetSecretValue
  - secretsmanager:PutSecretValue
  - secretsmanager:DescribeSecret
  - secretsmanager:UpdateSecretVersionStage
  - rds:ModifyDBInstance (RDS 암호 변경 시)
  - rds-db:connect (IAM 인증 방식 시)

교체 4단계:
  1. createSecret  - 새 암호 생성 (AWSPENDING 버전에 저장)
  2. setSecret     - 실제 DB 암호 변경
  3. testSecret    - 새 암호로 접속 테스트
  4. finishSecret  - AWSCURRENT로 버전 승격, AWSPREVIOUS로 이전 버전 이동

시크릿 구조 (JSON):
  {
    "engine": "mysql" | "postgres",
    "host": "your-rds-endpoint.rds.amazonaws.com",
    "port": 3306,
    "username": "admin",
    "password": "current-password",
    "dbname": "mydb"
  }

환경 변수:
  - EXCLUDE_CHARACTERS: 암호에서 제외할 문자 (기본: /@"'\\)
  - PASSWORD_LENGTH: 암호 길이 (기본: 32)
"""

import boto3
import json
import logging
import os
import secrets
import string
from typing import Optional

logger = logging.getLogger()
logger.setLevel(logging.INFO)

sm = boto3.client("secretsmanager")

EXCLUDE_CHARACTERS = os.environ.get("EXCLUDE_CHARACTERS", "/@\"'\\")
PASSWORD_LENGTH = int(os.environ.get("PASSWORD_LENGTH", "32"))


# ─── 암호 생성 ────────────────────────────────────────────────────────────────

def generate_password(length: int = PASSWORD_LENGTH) -> str:
    """강력한 랜덤 암호 생성"""
    alphabet = string.ascii_letters + string.digits + string.punctuation
    # 제외 문자 필터링
    alphabet = "".join(c for c in alphabet if c not in EXCLUDE_CHARACTERS)

    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        # 최소 조건: 대문자, 소문자, 숫자 각 1개 이상
        if (
            any(c.isupper() for c in password)
            and any(c.islower() for c in password)
            and any(c.isdigit() for c in password)
        ):
            return password


# ─── 시크릿 조회 ──────────────────────────────────────────────────────────────

def get_secret_value(secret_arn: str, stage: str = "AWSCURRENT", token: Optional[str] = None) -> dict:
    """시크릿 값 조회"""
    kwargs = {"SecretId": secret_arn, "VersionStage": stage}
    if token:
        kwargs["VersionId"] = token

    resp = sm.get_secret_value(**kwargs)
    return json.loads(resp["SecretString"])


# ─── DB 연결 테스트 ───────────────────────────────────────────────────────────

def test_mysql_connection(secret: dict) -> bool:
    """MySQL/MariaDB 연결 테스트"""
    try:
        import pymysql
        conn = pymysql.connect(
            host=secret["host"],
            port=int(secret.get("port", 3306)),
            user=secret["username"],
            password=secret["password"],
            database=secret.get("dbname", ""),
            connect_timeout=5,
        )
        conn.close()
        return True
    except Exception as e:
        logger.error("MySQL 연결 실패: %s", e)
        return False


def test_postgres_connection(secret: dict) -> bool:
    """PostgreSQL 연결 테스트"""
    try:
        import psycopg2
        conn = psycopg2.connect(
            host=secret["host"],
            port=int(secret.get("port", 5432)),
            user=secret["username"],
            password=secret["password"],
            dbname=secret.get("dbname", "postgres"),
            connect_timeout=5,
        )
        conn.close()
        return True
    except Exception as e:
        logger.error("PostgreSQL 연결 실패: %s", e)
        return False


def test_connection(secret: dict) -> bool:
    """DB 엔진 유형에 따라 연결 테스트"""
    engine = secret.get("engine", "").lower()

    if engine in ("mysql", "mariadb", "aurora-mysql"):
        return test_mysql_connection(secret)
    elif engine in ("postgres", "postgresql", "aurora-postgresql"):
        return test_postgres_connection(secret)
    else:
        logger.warning("지원하지 않는 엔진: %s, 연결 테스트 건너뜀", engine)
        return True


# ─── DB 암호 변경 ─────────────────────────────────────────────────────────────

def set_mysql_password(secret: dict, new_password: str) -> None:
    """MySQL 암호 변경"""
    import pymysql

    conn = pymysql.connect(
        host=secret["host"],
        port=int(secret.get("port", 3306)),
        user=secret["username"],
        password=secret["password"],  # 현재 암호로 접속
        connect_timeout=5,
    )
    try:
        with conn.cursor() as cursor:
            # MySQL 8.0+
            cursor.execute(
                "ALTER USER %s@'%%' IDENTIFIED BY %s",
                (secret["username"], new_password),
            )
        conn.commit()
        logger.info("MySQL 암호 변경 완료: %s", secret["username"])
    finally:
        conn.close()


def set_postgres_password(secret: dict, new_password: str) -> None:
    """PostgreSQL 암호 변경"""
    import psycopg2

    conn = psycopg2.connect(
        host=secret["host"],
        port=int(secret.get("port", 5432)),
        user=secret["username"],
        password=secret["password"],
        dbname=secret.get("dbname", "postgres"),
        connect_timeout=5,
    )
    conn.autocommit = True
    try:
        with conn.cursor() as cursor:
            # psycopg2는 파라미터 바인딩이 identity에 안 됨 → 안전한 방식으로 처리
            cursor.execute(
                f"ALTER USER \"{secret['username']}\" WITH PASSWORD %s",
                (new_password,),
            )
        logger.info("PostgreSQL 암호 변경 완료: %s", secret["username"])
    finally:
        conn.close()


def set_database_password(current_secret: dict, new_password: str) -> None:
    """DB 엔진에 따라 암호 변경"""
    engine = current_secret.get("engine", "").lower()

    if engine in ("mysql", "mariadb", "aurora-mysql"):
        set_mysql_password(current_secret, new_password)
    elif engine in ("postgres", "postgresql", "aurora-postgresql"):
        set_postgres_password(current_secret, new_password)
    else:
        raise ValueError(f"지원하지 않는 엔진: {engine}")


# ─── 4단계 교체 프로세스 ────────────────────────────────────────────────────

def create_secret(secret_arn: str, token: str) -> None:
    """
    1단계: 새 암호를 AWSPENDING 버전에 저장
    이미 AWSPENDING이 있으면 건너뜀 (멱등성)
    """
    try:
        # 이미 AWSPENDING에 값이 있으면 건너뜀
        sm.get_secret_value(SecretId=secret_arn, VersionId=token, VersionStage="AWSPENDING")
        logger.info("AWSPENDING 이미 존재, 건너뜀")
        return
    except sm.exceptions.ResourceNotFoundException:
        pass

    # 현재 시크릿 읽어서 암호만 교체
    current = get_secret_value(secret_arn, "AWSCURRENT")
    new_password = generate_password()
    current["password"] = new_password

    sm.put_secret_value(
        SecretId=secret_arn,
        ClientRequestToken=token,
        SecretString=json.dumps(current),
        VersionStages=["AWSPENDING"],
    )
    logger.info("새 암호 생성 및 AWSPENDING 저장 완료")


def set_secret(secret_arn: str, token: str) -> None:
    """
    2단계: 실제 DB 암호를 AWSPENDING의 새 암호로 변경
    """
    current_secret = get_secret_value(secret_arn, "AWSCURRENT")
    pending_secret = get_secret_value(secret_arn, "AWSPENDING", token)

    # 이미 새 암호가 적용된 경우 건너뜀
    if current_secret["password"] == pending_secret["password"]:
        logger.info("암호가 이미 동일, 건너뜀")
        return

    set_database_password(current_secret, pending_secret["password"])
    logger.info("DB 암호 변경 완료")


def test_secret(secret_arn: str, token: str) -> None:
    """
    3단계: 새 암호로 DB 접속 테스트
    실패 시 예외를 발생시켜 교체 중단
    """
    pending_secret = get_secret_value(secret_arn, "AWSPENDING", token)

    if not test_connection(pending_secret):
        raise RuntimeError("새 암호로 DB 접속 테스트 실패. 교체 중단.")

    logger.info("DB 접속 테스트 성공")


def finish_secret(secret_arn: str, token: str) -> None:
    """
    4단계: AWSPENDING → AWSCURRENT로 승격
    이전 AWSCURRENT는 AWSPREVIOUS로 이동
    """
    # 현재 버전 확인
    metadata = sm.describe_secret(SecretId=secret_arn)
    current_version = None

    for version_id, stages in metadata.get("VersionIdsToStages", {}).items():
        if "AWSCURRENT" in stages:
            if version_id == token:
                logger.info("이미 AWSCURRENT, 건너뜀")
                return
            current_version = version_id
            break

    # 버전 승격
    sm.update_secret_version_stage(
        SecretId=secret_arn,
        VersionStage="AWSCURRENT",
        MoveToVersionId=token,
        RemoveFromVersionId=current_version,
    )
    logger.info("AWSPENDING → AWSCURRENT 승격 완료")


# ─── 핸들러 ───────────────────────────────────────────────────────────────────

def lambda_handler(event: dict, context) -> None:
    """
    Secrets Manager Rotation Lambda 핸들러
    step에 따라 4단계 교체 프로세스 수행
    """
    secret_arn = event["SecretId"]
    token = event["ClientRequestToken"]
    step = event["Step"]

    logger.info("교체 시작: step=%s, secret=%s", step, secret_arn)

    # 교체 활성화 여부 확인
    metadata = sm.describe_secret(SecretId=secret_arn)
    if not metadata.get("RotationEnabled"):
        raise ValueError(f"시크릿에 Rotation이 활성화되어 있지 않음: {secret_arn}")

    # 토큰이 유효한지 확인
    versions = metadata.get("VersionIdsToStages", {})
    if token not in versions:
        raise ValueError(f"유효하지 않은 토큰: {token}")

    if "AWSCURRENT" in versions[token]:
        logger.info("이미 AWSCURRENT, 완료")
        return

    if "AWSPENDING" not in versions[token]:
        raise ValueError(f"토큰이 AWSPENDING 아님: {token}")

    # 단계별 실행
    if step == "createSecret":
        create_secret(secret_arn, token)
    elif step == "setSecret":
        set_secret(secret_arn, token)
    elif step == "testSecret":
        test_secret(secret_arn, token)
    elif step == "finishSecret":
        finish_secret(secret_arn, token)
    else:
        raise ValueError(f"알 수 없는 step: {step}")

    logger.info("단계 완료: %s", step)
