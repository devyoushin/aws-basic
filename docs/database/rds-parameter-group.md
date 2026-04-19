# RDS 파라미터 그룹 튜닝

## 1. 개요

RDS 파라미터 그룹은 데이터베이스 엔진의 동작을 제어하는 설정 묶음이다.
기본 파라미터 그룹은 범용 설정이라 워크로드 특성에 맞게 튜닝하지 않으면 성능 병목, 연결 고갈, 느린 쿼리 문제가 발생한다.
파라미터 변경 후 Static(재시작 필요) vs Dynamic(즉시 적용) 구분이 중요하며, 프로덕션 적용 전 스테이징 검증이 필수다.

---

## 2. 설명

### 2.1 핵심 개념

**파라미터 타입**

| 타입 | 적용 시점 | 예시 |
|------|---------|------|
| Static | DB 재시작 후 적용 | `max_connections`, `innodb_buffer_pool_size` |
| Dynamic | 즉시 적용 (재시작 불필요) | `slow_query_log`, `long_query_time` |

**MySQL/Aurora MySQL 주요 튜닝 파라미터**

| 파라미터 | 기본값 | 권장값 | 설명 |
|---------|--------|--------|------|
| `max_connections` | LEAST({DBInstanceClassMemory/12582880}, 5000) | 인스턴스별 산출 | 메모리 기반 자동 계산 권장 |
| `innodb_buffer_pool_size` | {DBInstanceClassMemory*3/4} | 75~80% of RAM | 가장 중요한 성능 파라미터 |
| `slow_query_log` | 0 | 1 | 슬로우 쿼리 로깅 활성화 |
| `long_query_time` | 10 | 1 | 1초 이상 쿼리를 슬로우로 기록 |
| `innodb_flush_log_at_trx_commit` | 1 | 1 (프로덕션) | 0/2는 성능 좋지만 데이터 손실 위험 |
| `character_set_server` | latin1 | utf8mb4 | 이모지 포함 유니코드 완전 지원 |
| `time_zone` | UTC | Asia/Seoul | 서버 타임존 |
| `binlog_format` | MIXED | ROW | 복제 안정성 (Aurora에서는 ROW 기본) |
| `wait_timeout` | 28800 (8h) | 300 | 유휴 연결 정리 (연결 풀 누수 방지) |
| `interactive_timeout` | 28800 | 300 | 인터랙티브 연결 타임아웃 |

**PostgreSQL 주요 튜닝 파라미터**

| 파라미터 | 기본값 | 권장값 | 설명 |
|---------|--------|--------|------|
| `shared_buffers` | {DBInstanceClassMemory/32768}8KB | 25% of RAM | PostgreSQL 캐시 |
| `effective_cache_size` | - | 75% of RAM | 쿼리 플래너 힌트 |
| `work_mem` | 4096 | 16384~65536 | 정렬/해시 연산 메모리 |
| `maintenance_work_mem` | 65536 | 256MB+ | VACUUM, CREATE INDEX 메모리 |
| `max_connections` | 100 | 100~500 | PgBouncer 사용 시 낮게 유지 |
| `log_min_duration_statement` | -1 | 1000 | 1초 이상 쿼리 로깅 (ms 단위) |
| `autovacuum_vacuum_scale_factor` | 0.2 | 0.05 | 대용량 테이블에서 더 자주 VACUUM |
| `checkpoint_completion_target` | 0.5 | 0.9 | 체크포인트 IO 분산 |
| `wal_buffers` | -1 | 16MB | WAL 버퍼 |

---

### 2.2 실무 적용 코드

**Terraform — MySQL 파라미터 그룹**

```hcl
resource "aws_db_parameter_group" "mysql" {
  name        = "mysql80-production"
  family      = "mysql8.0"
  description = "Production MySQL 8.0 parameters"

  # 슬로우 쿼리 로깅 활성화
  parameter {
    name  = "slow_query_log"
    value = "1"
    apply_method = "immediate"   # Dynamic
  }

  parameter {
    name  = "long_query_time"
    value = "1"
    apply_method = "immediate"
  }

  parameter {
    name  = "log_output"
    value = "FILE"   # FILE: CloudWatch로 전송 가능, TABLE: mysql.slow_log 테이블
    apply_method = "immediate"
  }

  # 연결 타임아웃 (유휴 연결 정리)
  parameter {
    name  = "wait_timeout"
    value = "300"
    apply_method = "immediate"
  }

  parameter {
    name  = "interactive_timeout"
    value = "300"
    apply_method = "immediate"
  }

  # 문자셋
  parameter {
    name  = "character_set_server"
    value = "utf8mb4"
    apply_method = "pending-reboot"   # Static
  }

  parameter {
    name  = "collation_server"
    value = "utf8mb4_unicode_ci"
    apply_method = "pending-reboot"
  }

  # 타임존
  parameter {
    name  = "time_zone"
    value = "Asia/Seoul"
    apply_method = "immediate"
  }

  # 바이너리 로그 보관 (복제/PITR 관련)
  parameter {
    name  = "binlog_format"
    value = "ROW"
    apply_method = "pending-reboot"
  }

  # 일반 쿼리 로그 (성능 분석용, 프로덕션에서는 비활성화)
  parameter {
    name  = "general_log"
    value = "0"
    apply_method = "immediate"
  }

  tags = { Environment = "production" }
}

resource "aws_db_instance" "main" {
  identifier        = "prod-mysql"
  engine            = "mysql"
  engine_version    = "8.0"
  instance_class    = "db.r6g.xlarge"
  parameter_group_name = aws_db_parameter_group.mysql.name

  # 슬로우 쿼리 로그를 CloudWatch Logs로 전송
  enabled_cloudwatch_logs_exports = ["slowquery", "error", "general"]

  # ...
}
```

**Terraform — PostgreSQL 파라미터 그룹**

```hcl
resource "aws_db_parameter_group" "postgres" {
  name   = "postgres15-production"
  family = "postgres15"

  parameter {
    name  = "log_min_duration_statement"
    value = "1000"   # 1초 이상 쿼리 로깅
    apply_method = "immediate"
  }

  parameter {
    name  = "log_connections"
    value = "1"
    apply_method = "immediate"
  }

  parameter {
    name  = "log_disconnections"
    value = "1"
    apply_method = "immediate"
  }

  parameter {
    name  = "autovacuum_vacuum_scale_factor"
    value = "0.05"   # 5% 변경 시 VACUUM (기본 20%보다 자주)
    apply_method = "immediate"
  }

  parameter {
    name  = "autovacuum_analyze_scale_factor"
    value = "0.02"
    apply_method = "immediate"
  }

  parameter {
    name  = "checkpoint_completion_target"
    value = "0.9"
    apply_method = "immediate"
  }

  parameter {
    name  = "work_mem"
    value = "32768"   # 32MB (정렬 쿼리 성능)
    apply_method = "immediate"
  }

  parameter {
    name  = "maintenance_work_mem"
    value = "262144"   # 256MB (VACUUM, INDEX 생성)
    apply_method = "immediate"
  }

  parameter {
    name  = "timezone"
    value = "Asia/Seoul"
    apply_method = "pending-reboot"
  }
}
```

**슬로우 쿼리 분석**

```bash
# MySQL 슬로우 쿼리 로그 CloudWatch에서 확인
aws logs filter-log-events \
  --log-group-name /aws/rds/instance/prod-mysql/slowquery \
  --filter-pattern "Query_time" \
  --start-time $(date -d '1 hour ago' +%s000)

# MySQL Performance Schema로 상위 슬로우 쿼리 확인
SELECT digest_text, count_star, avg_timer_wait/1e12 AS avg_sec,
       sum_rows_examined, sum_rows_sent
FROM performance_schema.events_statements_summary_by_digest
ORDER BY avg_timer_wait DESC
LIMIT 20;

# PostgreSQL pg_stat_statements 활성화
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
SELECT query, calls, total_exec_time/calls AS avg_ms,
       rows/calls AS avg_rows
FROM pg_stat_statements
ORDER BY avg_ms DESC
LIMIT 20;
```

**max_connections 계산 (MySQL)**

```bash
# 인스턴스별 권장 max_connections
# db.r6g.large: 13,000MB → ~1036
# db.r6g.xlarge: 26,000MB → ~2072
# db.r6g.2xlarge: 52,000MB → ~4144

# 현재 연결 수 모니터링
mysql -e "SHOW STATUS LIKE 'Threads_connected';"
mysql -e "SHOW PROCESSLIST;" | wc -l
```

---

### 2.3 보안/비용 Best Practice

- **기본 파라미터 그룹 사용 금지**: 기본 그룹은 수정 불가. 반드시 커스텀 그룹을 생성해 RDS에 연결
- **슬로우 쿼리 로그 항상 활성화**: `long_query_time=1`이면 오버헤드 거의 없음. 성능 문제 조기 발견에 필수
- **`wait_timeout` 단축 필수**: 기본 8시간이면 애플리케이션 연결 풀이 끊어진 연결을 계속 보유. 300초(5분)으로 설정
- **파라미터 변경 전 스테이징 검증**: Static 파라미터는 재시작 필요. 프로덕션 재시작 전 스테이징에서 영향도 확인

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**Too many connections 오류**

```bash
# 현재 연결 수 확인
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name DatabaseConnections \
  --dimensions Name=DBInstanceIdentifier,Value=prod-mysql \
  --start-time $(date -d '1 hour ago' -u +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 60 \
  --statistics Maximum

# 연결 점유 프로세스 확인
SHOW PROCESSLIST;
SHOW STATUS LIKE 'Threads%';

# 해결: 애플리케이션 커넥션 풀 설정 확인 + RDS Proxy 도입
# RDS Proxy: 연결 풀링으로 실제 DB 연결 수를 줄임
```

**PostgreSQL VACUUM bloat 문제**

```bash
# 테이블 bloat 확인
SELECT schemaname, tablename,
       pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size,
       n_dead_tup, n_live_tup,
       round(100*n_dead_tup::numeric/NULLIF(n_live_tup+n_dead_tup,0), 2) AS dead_pct
FROM pg_stat_user_tables
WHERE n_dead_tup > 10000
ORDER BY n_dead_tup DESC;

# 수동 VACUUM
VACUUM ANALYZE tablename;
VACUUM VERBOSE ANALYZE tablename;   -- 상세 출력
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: 파라미터 그룹을 기존 RDS에 교체하면 즉시 적용되나요?**
A: 파라미터 그룹 자체를 교체해도 Static 파라미터는 DB 재시작 전까지 적용 안 됩니다. Dynamic 파라미터는 즉시 적용됩니다. RDS 콘솔에서 "Pending reboot" 상태로 표시됩니다.

**Q: Aurora는 파라미터 그룹이 DB Cluster와 DB Instance 두 종류인데 차이는?**
A: Cluster Parameter Group은 클러스터 전체 공통 설정(binlog_format 등), Instance Parameter Group은 개별 인스턴스 설정(max_connections 등)입니다. Aurora에서는 Cluster 수준 설정이 우선입니다.

---

## 4. 모니터링 및 알람

```hcl
# 연결 수 임계값 알람
resource "aws_cloudwatch_metric_alarm" "db_connections" {
  alarm_name          = "rds-connections-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "DatabaseConnections"
  namespace           = "AWS/RDS"
  period              = 60
  statistic           = "Maximum"
  threshold           = 400   # max_connections의 80%

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.id
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
}

# 슬로우 쿼리 급증 알람
resource "aws_cloudwatch_log_metric_filter" "slow_query" {
  name           = "rds-slow-query-count"
  pattern        = "Query_time"
  log_group_name = "/aws/rds/instance/prod-mysql/slowquery"

  metric_transformation {
    name      = "SlowQueryCount"
    namespace = "Custom/RDS"
    value     = "1"
  }
}
```

---

## 5. TIP

- **Performance Insights**: RDS Performance Insights를 활성화하면 DB 부하를 시각적으로 분석. 슬로우 쿼리 없이도 CPU/IO 병목 원인을 빠르게 파악
- **RDS Proxy**: Lambda나 Auto Scaling 환경처럼 연결 수가 급변할 때 RDS Proxy로 연결 풀링. max_connections 초과 없이 수천 개 연결 처리
- **파라미터 변경 이력 관리**: Terraform state로 관리하면 변경 이력 추적 가능. CloudTrail에도 파라미터 변경이 기록됨
- **Aurora Serverless v2**: 워크로드가 불규칙한 경우 프로비저닝 RDS 대신 Aurora Serverless v2로 비용 최적화 (0.5 ACU 최소 단위로 자동 스케일)
