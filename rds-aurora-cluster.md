# RDS Aurora 클러스터 운영

## 1. 개요

Aurora는 MySQL/PostgreSQL과 호환되면서 스토리지를 클러스터 내 모든 인스턴스가 공유하는 AWS 전용 관계형 DB다.
스토리지 레이어가 6개 AZ에 자동 복제되어 일반 RDS보다 내구성이 높고, 페일오버가 30초 내외로 빠르다.
Writer/Reader 엔드포인트 분리, 클론, 글로벌 데이터베이스 등 운영 편의 기능이 풍부하다.

---

## 2. 설명

### 2.1 핵심 개념

**Aurora vs RDS 비교**

| 항목 | Aurora | RDS (MySQL/PostgreSQL) |
|------|--------|----------------------|
| 스토리지 복제 | 3AZ × 2 copy = 6 copy 자동 | Multi-AZ 시 동기 복제 1copy |
| 페일오버 시간 | ~30초 (DNS 절체) | 1~2분 |
| 읽기 복제본 | 최대 15개, 동일 스토리지 공유 | 최대 5개, 비동기 복제 |
| 스토리지 | 10GB~128TB 자동 확장 | 수동 조정 |
| 비용 | RDS 대비 20~25% 비쌈 | 더 저렴 |
| 글로벌 DB | 지원 (1초 미만 복제 지연) | 미지원 |

**엔드포인트 유형**

| 엔드포인트 | 용도 | 특징 |
|---------|------|------|
| Cluster Endpoint (Writer) | 쓰기/읽기 모두 | 페일오버 시 자동으로 새 Writer 가리킴 |
| Reader Endpoint | 읽기 전용 | 읽기 복제본들에 Round-Robin 부하 분산 |
| Instance Endpoint | 특정 인스턴스 직접 | 디버깅 용도, 운영에서는 비권장 |
| Custom Endpoint | 특정 인스턴스 그룹 | 대용량 분석 쿼리용 고사양 Reader 분리 |

**Aurora 페일오버 동작**

```
1. Writer 인스턴스 장애 감지 (~10초)
2. 가장 최신 Reader를 Writer로 승격 (~20초)
3. Cluster Endpoint DNS 업데이트
4. 구 Writer 복구 후 Reader로 재합류

총 소요: ~30초 (DNS TTL 5초로 빠른 절체)
→ 애플리케이션 재연결 로직 + 짧은 retry 필수
```

---

### 2.2 실무 적용 코드

**Terraform — Aurora MySQL 클러스터**

```hcl
resource "aws_rds_cluster" "aurora" {
  cluster_identifier = "prod-aurora-mysql"
  engine             = "aurora-mysql"
  engine_version     = "8.0.mysql_aurora.3.04.0"

  database_name   = "appdb"
  master_username = "admin"
  master_password = var.db_password   # Secrets Manager 관리 권장

  # 스토리지 암호화
  storage_encrypted = true
  kms_key_id        = aws_kms_key.rds.arn

  # 네트워크
  vpc_security_group_ids = [aws_security_group.rds.id]
  db_subnet_group_name   = aws_db_subnet_group.main.name
  availability_zones     = ["ap-northeast-2a", "ap-northeast-2b", "ap-northeast-2c"]

  # 파라미터 그룹
  db_cluster_parameter_group_name = aws_rds_cluster_parameter_group.aurora.name

  # 백업
  backup_retention_period   = 7      # 7일 자동 백업
  preferred_backup_window   = "18:00-19:00"   # UTC (KST 03:00-04:00)
  preferred_maintenance_window = "sun:19:00-sun:20:00"

  # 삭제 보호
  deletion_protection = true
  skip_final_snapshot = false
  final_snapshot_identifier = "prod-aurora-final-snapshot"

  # CloudWatch 로그 내보내기
  enabled_cloudwatch_logs_exports = ["audit", "error", "slowquery", "general"]

  tags = { Environment = "production" }
}

# Writer 인스턴스
resource "aws_rds_cluster_instance" "writer" {
  identifier         = "prod-aurora-writer"
  cluster_identifier = aws_rds_cluster.aurora.id
  instance_class     = "db.r6g.xlarge"
  engine             = aws_rds_cluster.aurora.engine

  # Performance Insights 활성화
  performance_insights_enabled          = true
  performance_insights_retention_period = 7   # 7일 무료 (731일은 유료)

  # Enhanced Monitoring (1초 간격)
  monitoring_interval = 60
  monitoring_role_arn = aws_iam_role.rds_monitoring.arn

  auto_minor_version_upgrade = true

  tags = { Role = "writer" }
}

# Reader 인스턴스 (읽기 부하 분산)
resource "aws_rds_cluster_instance" "reader" {
  count = 2   # AZ별 1개씩

  identifier         = "prod-aurora-reader-${count.index}"
  cluster_identifier = aws_rds_cluster.aurora.id
  instance_class     = "db.r6g.large"   # 읽기는 더 작은 인스턴스 가능
  engine             = aws_rds_cluster.aurora.engine

  performance_insights_enabled = true

  tags = { Role = "reader" }
}

# DB Subnet Group
resource "aws_db_subnet_group" "main" {
  name       = "aurora-subnet-group"
  subnet_ids = aws_subnet.isolated[*].id   # Isolated subnet 사용

  tags = { Name = "aurora-subnet-group" }
}
```

**Aurora 클러스터 파라미터 그룹**

```hcl
resource "aws_rds_cluster_parameter_group" "aurora" {
  name   = "aurora-mysql80-cluster"
  family = "aurora-mysql8.0"

  parameter {
    name  = "binlog_format"
    value = "ROW"
    apply_method = "pending-reboot"
  }

  parameter {
    name  = "character_set_server"
    value = "utf8mb4"
    apply_method = "pending-reboot"
  }

  parameter {
    name  = "time_zone"
    value = "Asia/Seoul"
    apply_method = "immediate"
  }

  parameter {
    name  = "slow_query_log"
    value = "1"
    apply_method = "immediate"
  }

  parameter {
    name  = "long_query_time"
    value = "1"
    apply_method = "immediate"
  }
}
```

**Aurora Auto Scaling (Reader 자동 추가/제거)**

```hcl
resource "aws_appautoscaling_target" "aurora_reader" {
  service_namespace  = "rds"
  resource_id        = "cluster:${aws_rds_cluster.aurora.cluster_identifier}"
  scalable_dimension = "rds:cluster:ReadReplicaCount"
  min_capacity       = 1
  max_capacity       = 5
}

resource "aws_appautoscaling_policy" "aurora_reader" {
  name               = "aurora-reader-scale"
  service_namespace  = "rds"
  resource_id        = aws_appautoscaling_target.aurora_reader.resource_id
  scalable_dimension = aws_appautoscaling_target.aurora_reader.scalable_dimension
  policy_type        = "TargetTrackingScaling"

  target_tracking_scaling_policy_configuration {
    target_value = 70.0   # CPU 70% 기준

    predefined_metric_specification {
      predefined_metric_type = "RDSReaderAverageCPUUtilization"
    }

    scale_in_cooldown  = 300
    scale_out_cooldown = 300
  }
}
```

**Aurora 클론 (Zero-Copy 스냅샷 대안)**

```bash
# AWS CLI로 Aurora 클론 생성 (거의 즉시, 스토리지 공유 — 분기 이후 변경분만 비용)
aws rds restore-db-cluster-to-point-in-time \
  --source-db-cluster-identifier prod-aurora-mysql \
  --db-cluster-identifier staging-aurora-clone \
  --restore-type copy-on-write \
  --use-latest-restorable-time \
  --vpc-security-group-ids sg-xxxxxxxx \
  --db-subnet-group-name aurora-subnet-group
```

**Secrets Manager 패스워드 자동 교체**

```hcl
resource "aws_secretsmanager_secret" "aurora_password" {
  name = "prod/aurora/master-password"

  # 30일마다 자동 교체
  rotation_rules {
    automatically_after_days = 30
  }
}

# RDS와 Secrets Manager 연동 (RDS가 직접 자격증명 관리)
resource "aws_rds_cluster" "aurora" {
  # ...
  manage_master_user_password = true   # Secrets Manager 자동 연동
}
```

---

### 2.3 보안/비용 Best Practice

- **Reader 엔드포인트로 읽기 분산**: 모든 쿼리를 Writer로 보내면 Writer 병목. 읽기 전용 쿼리는 반드시 Reader 엔드포인트로
- **Custom Endpoint로 분석 쿼리 격리**: OLAP성 무거운 쿼리를 대형 Reader 인스턴스 그룹의 Custom Endpoint로 분리. 운영 쿼리 영향 없음
- **Aurora Serverless v2 고려**: 트래픽이 불규칙하면 Serverless v2로 0.5~128 ACU 자동 스케일. 최소 비용 기준 매우 낮음
- **백업 윈도우 = 유지보수 윈도우 피하기**: 둘이 겹치면 동시에 I/O 영향. 트래픽이 가장 적은 새벽에 분리 배치

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**페일오버 후 애플리케이션 연결 실패**

```bash
# 원인 1: 애플리케이션이 Writer DNS를 캐싱 (TTL 무시)
# → JDBC URL에 useSSL=false&allowPublicKeyRetrieval=true 대신
#    connectTimeout과 socketTimeout 설정 확인
# → Java: jdbc:mysql://cluster-endpoint?connectTimeout=3000&socketTimeout=30000

# 원인 2: Connection Pool이 죽은 연결 유지
# → HikariCP: keepaliveTime, connectionTimeout 설정
# → Spring: testOnBorrow, validationQuery 활성화

# 현재 Writer 확인
aws rds describe-db-clusters \
  --db-cluster-identifier prod-aurora-mysql \
  --query 'DBClusters[0].{Writer:DBClusterMembers[?IsClusterWriter==`true`].DBInstanceIdentifier|[0]}'
```

**Reader 지연(Replica Lag) 높음**

```bash
# CloudWatch에서 Aurora Replica Lag 확인
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name AuroraReplicaLag \
  --dimensions Name=DBInstanceIdentifier,Value=prod-aurora-reader-0 \
  --start-time $(date -d '1 hour ago' -u +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 60 \
  --statistics Maximum

# 원인: 대량 쓰기 작업 시 일시적으로 증가 (Aurora는 보통 수ms)
# 해결: 읽기 쿼리에 일시적 직접 Writer 사용, 또는 대량 쓰기 속도 조절
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: Aurora 스토리지 비용이 생각보다 높습니다**
A: Aurora는 데이터가 삭제되어도 스토리지가 즉시 줄지 않고 최고점이 유지됩니다. `OPTIMIZE TABLE`(MySQL) 또는 `VACUUM FULL`(PostgreSQL)로 스토리지를 회수하거나, 클론을 새로 만들어 마이그레이션하는 방법이 있습니다.

**Q: Aurora Global Database와 Cross-Region Read Replica 차이는?**
A: Global Database는 전용 복제 인프라로 ~1초 미만의 복제 지연과 자동 페일오버를 제공합니다. Cross-Region Read Replica는 표준 복제라 지연이 더 크고 페일오버가 수동입니다.

---

## 4. 모니터링 및 알람

```hcl
# Aurora Replica Lag 알람
resource "aws_cloudwatch_metric_alarm" "replica_lag" {
  alarm_name          = "aurora-replica-lag-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "AuroraReplicaLag"
  namespace           = "AWS/RDS"
  period              = 60
  statistic           = "Maximum"
  threshold           = 1000   # 1초 이상 지연

  dimensions = {
    DBClusterIdentifier = aws_rds_cluster.aurora.cluster_identifier
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
}

# Freeable Memory 알람
resource "aws_cloudwatch_metric_alarm" "free_memory" {
  alarm_name          = "aurora-low-memory"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 3
  metric_name         = "FreeableMemory"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Minimum"
  threshold           = 268435456   # 256MB

  dimensions = {
    DBInstanceIdentifier = aws_rds_cluster_instance.writer.identifier
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
}
```

---

## 5. TIP

- **Aurora Backtrack**: Aurora MySQL에서 특정 시점으로 빠르게 되돌리는 기능 (PITR과 달리 새 클러스터 생성 없이 수초~수분 내 완료). `backtrack_window` 파라미터로 활성화
- **Fast Clone으로 개발/스테이징 DB 관리**: Clone은 Copy-on-Write라 초기에 스토리지 거의 안 씀. 프로덕션 데이터로 테스트 환경 빠르게 구성 가능
- **Zero-ETL Integration**: Aurora MySQL → Redshift 실시간 복제. 별도 ETL 파이프라인 없이 데이터 웨어하우스 연동 가능
- **RDS Proxy 필수 조건**: Lambda + Aurora 조합이면 RDS Proxy가 필수. Lambda가 폭발적으로 늘면 DB 연결 수 초과로 장애 발생
