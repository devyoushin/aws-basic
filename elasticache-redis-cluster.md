# ElastiCache Redis 클러스터 모드 운영

## 1. 개요

ElastiCache Redis는 인메모리 캐시/세션 스토어/메시지 큐로 활용되는 관리형 Redis 서비스다.
클러스터 모드(Cluster Mode Enabled)를 쓰면 데이터를 여러 샤드에 분산해 수평 확장이 가능하고,
클러스터 모드 비활성화(Replication Group)는 단순 Primary+Replica 구조로 읽기 부하 분산에 적합하다.
페일오버 동작, 메모리 정책, 연결 관리를 잘못 설정하면 캐시 폭풍 및 OOM 장애가 발생한다.

---

## 2. 설명

### 2.1 핵심 개념

**클러스터 모드 활성화 vs 비활성화**

| 항목 | Cluster Mode Disabled | Cluster Mode Enabled |
|------|----------------------|---------------------|
| 샤드 수 | 1개 | 1~500개 |
| 수평 확장 | 불가 (스케일 업만) | 샤드 추가/제거 |
| Multi-key 명령 | 모두 지원 | 동일 슬롯 키만 지원 |
| 장애 조치 | Primary→Replica 자동 | 샤드별 독립 페일오버 |
| 연결 방식 | 단일 Endpoint | Cluster Endpoint (슬롯 라우팅) |
| 권장 용도 | 단순 캐시, 세션 스토어 | 대용량 데이터, 높은 처리량 |

**메모리 정책 (maxmemory-policy)**

| 정책 | 동작 | 권장 용도 |
|------|------|---------|
| `allkeys-lru` | 모든 키 중 LRU 제거 | 일반적인 캐시 |
| `volatile-lru` | TTL 설정된 키 중 LRU 제거 | TTL 혼합 사용 시 |
| `allkeys-lfu` | 사용 빈도 낮은 키 제거 | 핫/콜드 데이터 혼합 |
| `volatile-ttl` | 가장 짧은 TTL 키 먼저 제거 | TTL 기반 만료 중심 |
| `noeviction` | 메모리 꽉 차면 쓰기 오류 | 세션 스토어 (유실 불가) |

**페일오버 동작**

```
정상 상태:
  Primary(AZ-a) ← Write
  Replica(AZ-b) ← Read (옵션)

Primary 장애:
  1. ElastiCache가 장애 감지 (~10초)
  2. Replica를 Primary로 승격 (~20초)
  3. Primary Endpoint DNS 업데이트
  4. 클라이언트 재연결

총 다운타임: ~30초 (Multi-AZ 활성화 시)
→ 애플리케이션 retry 로직 필수
```

---

### 2.2 실무 적용 코드

**Terraform — Redis Replication Group (Cluster Mode Disabled)**

```hcl
resource "aws_elasticache_replication_group" "redis" {
  replication_group_id = "prod-redis"
  description          = "Production Redis cache"

  node_type            = "cache.r6g.large"
  num_cache_clusters   = 2   # Primary 1 + Replica 1
  port                 = 6379

  # Multi-AZ 페일오버 활성화
  automatic_failover_enabled = true
  multi_az_enabled           = true

  # 네트워크
  subnet_group_name  = aws_elasticache_subnet_group.main.name
  security_group_ids = [aws_security_group.redis.id]

  # 암호화
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true   # TLS (in-transit)
  auth_token                 = var.redis_auth_token   # AUTH 패스워드

  # 파라미터 그룹
  parameter_group_name = aws_elasticache_parameter_group.redis.name

  # 유지보수 & 백업
  maintenance_window       = "sun:18:00-sun:19:00"   # UTC
  snapshot_window          = "16:00-17:00"
  snapshot_retention_limit = 3   # 3일 스냅샷 보관

  # 버전
  engine_version = "7.1"
  auto_minor_version_upgrade = true

  # CloudWatch 로그
  log_delivery_configuration {
    destination      = aws_cloudwatch_log_group.redis_slow.name
    destination_type = "cloudwatch-logs"
    log_format       = "text"
    log_type         = "slow-log"
  }

  log_delivery_configuration {
    destination      = aws_cloudwatch_log_group.redis_engine.name
    destination_type = "cloudwatch-logs"
    log_format       = "text"
    log_type         = "engine-log"
  }

  tags = { Environment = "production" }
}

resource "aws_elasticache_subnet_group" "main" {
  name       = "redis-subnet-group"
  subnet_ids = aws_subnet.isolated[*].id
}
```

**Terraform — Redis Cluster Mode Enabled (샤딩)**

```hcl
resource "aws_elasticache_replication_group" "redis_cluster" {
  replication_group_id = "prod-redis-cluster"
  description          = "Production Redis Cluster Mode"

  node_type = "cache.r6g.large"

  # 클러스터 모드 활성화
  num_node_groups         = 3   # 3개 샤드 (16384 슬롯을 3등분)
  replicas_per_node_group = 1   # 샤드당 Replica 1개

  automatic_failover_enabled = true
  multi_az_enabled           = true

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  auth_token                 = var.redis_auth_token

  parameter_group_name = aws_elasticache_parameter_group.redis_cluster.name
  subnet_group_name    = aws_elasticache_subnet_group.main.name
  security_group_ids   = [aws_security_group.redis.id]

  engine_version = "7.1"
}
```

**파라미터 그룹 설정**

```hcl
resource "aws_elasticache_parameter_group" "redis" {
  name   = "redis71-production"
  family = "redis7"

  # 메모리 정책
  parameter {
    name  = "maxmemory-policy"
    value = "allkeys-lru"
  }

  # 슬로우 쿼리 로깅 (10ms 이상)
  parameter {
    name  = "slowlog-log-slower-than"
    value = "10000"   # 마이크로초 (10,000μs = 10ms)
  }

  parameter {
    name  = "slowlog-max-len"
    value = "128"
  }

  # Lazy Freeing (메모리 해제를 비동기로 — 대형 키 삭제 시 블로킹 방지)
  parameter {
    name  = "lazyfree-lazy-eviction"
    value = "yes"
  }

  parameter {
    name  = "lazyfree-lazy-expire"
    value = "yes"
  }

  # 클라이언트 연결 출력 버퍼 (OOM 방지)
  parameter {
    name  = "client-output-buffer-limit"
    value = "normal 0 0 0 pubsub 32mb 8mb 60 replica 256mb 64mb 60"
  }
}
```

**애플리케이션 연결 (Java — Lettuce)**

```java
// Cluster Mode Disabled — 단일 엔드포인트
RedisClient client = RedisClient.create(
    RedisURI.builder()
        .withHost("prod-redis.xxxxxx.cache.amazonaws.com")
        .withPort(6379)
        .withSsl(true)
        .withPassword("auth-token")
        .withTimeout(Duration.ofSeconds(2))
        .build()
);

// Cluster Mode Enabled — Cluster Client
RedisClusterClient clusterClient = RedisClusterClient.create(
    RedisURI.builder()
        .withHost("clustercfg.prod-redis-cluster.xxxxxx.cache.amazonaws.com")
        .withPort(6379)
        .withSsl(true)
        .withPassword("auth-token")
        .build()
);
// ClusterClient는 슬롯 기반으로 올바른 노드에 자동 라우팅
```

---

### 2.3 보안/비용 Best Practice

- **`maxmemory-policy` 반드시 설정**: 기본값 `noeviction`이면 메모리 꽉 찰 때 모든 쓰기 오류. 캐시는 `allkeys-lru`, 세션 스토어는 `noeviction` + 충분한 메모리 확보
- **AUTH 토큰 + TLS 필수**: VPC 내부라도 네트워크 스니핑 대비. `transit_encryption_enabled + auth_token` 조합
- **Reserved Cache Node**: 운영 환경은 1년/3년 Reserved로 최대 40% 절감
- **키 크기와 개수 모니터링**: 대형 키(>100KB) 하나가 이벤트 루프를 블로킹. `SCAN + OBJECT ENCODING` 또는 `redis-memory-analyzer`로 주기적 분석

---

## 3. 트러블슈팅

### 3.1 주요 이슈

**Evictions 급증 (캐시 히트율 저하)**

```bash
# Evictions CloudWatch 지표 확인
aws cloudwatch get-metric-statistics \
  --namespace AWS/ElastiCache \
  --metric-name Evictions \
  --dimensions Name=CacheClusterId,Value=prod-redis-0001-001 \
  --start-time $(date -d '1 hour ago' -u +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 60 \
  --statistics Sum

# Redis CLI에서 메모리 사용 현황 확인
redis-cli -h prod-redis.xxxxxx.cache.amazonaws.com -a token INFO memory
redis-cli INFO stats | grep evicted_keys

# 해결 방법:
# 1. 인스턴스 스케일 업 (더 큰 node_type)
# 2. 불필요한 키 TTL 설정
# 3. 클러스터 모드로 샤드 수 증가
```

**ClusterDown 오류 (클러스터 모드)**

```bash
# 증상: "CLUSTERDOWN The cluster is down"
# 원인: 과반수 샤드가 Primary 없이 Replica만 남은 상태

# 클러스터 상태 확인
redis-cli -h clustercfg.prod-redis.cache.amazonaws.com \
  -p 6379 --tls --askpass \
  CLUSTER INFO

redis-cli CLUSTER NODES   # 각 노드 상태 확인

# 수동 페일오버 (Replica를 Primary로 강제 승격)
redis-cli -h replica-node-endpoint CLUSTER FAILOVER FORCE
```

**대형 키로 인한 지연 (Latency spike)**

```bash
# 슬로우 로그 확인
redis-cli SLOWLOG GET 10

# 대형 키 탐지
redis-cli --bigkeys   # 자동으로 전체 스캔 (운영 시간에는 주의)

# 특정 키 크기 확인
redis-cli MEMORY USAGE my-key
redis-cli OBJECT ENCODING my-key
redis-cli OBJECT IDLETIME my-key
```

### 3.2 자주 발생하는 문제 (Q&A)

**Q: 클러스터 모드에서 MGET/MSET이 안 됩니다**
A: 클러스터 모드에서 다중 키 명령은 모든 키가 동일한 해시 슬롯에 있어야 합니다. Hash Tags를 사용해 키를 같은 슬롯에 배치하세요: `{user:1}:session`, `{user:1}:profile` → `user:1` 부분으로 슬롯 결정.

**Q: 페일오버 후 애플리케이션이 연결을 회복 못 합니다**
A: DNS TTL이 짧아도(5초) 클라이언트 DNS 캐시가 오래 남을 수 있습니다. Lettuce/Jedis의 `reconnectDelay`와 `retryAttempts` 설정을 확인하세요. Spring Cache의 `@Retryable`과 조합을 권장합니다.

---

## 4. 모니터링 및 알람

```hcl
# 메모리 사용률 알람
resource "aws_cloudwatch_metric_alarm" "redis_memory" {
  alarm_name          = "redis-memory-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "DatabaseMemoryUsagePercentage"
  namespace           = "AWS/ElastiCache"
  period              = 300
  statistic           = "Average"
  threshold           = 75   # 75% 이상 시 알람

  dimensions = {
    ReplicationGroupId = aws_elasticache_replication_group.redis.id
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
}

# 캐시 히트율 알람
resource "aws_cloudwatch_metric_alarm" "redis_hit_rate" {
  alarm_name          = "redis-cache-hit-low"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 5
  metric_name         = "CacheHitRate"
  namespace           = "AWS/ElastiCache"
  period              = 300
  statistic           = "Average"
  threshold           = 80   # 히트율 80% 미만 시 알람

  dimensions = {
    ReplicationGroupId = aws_elasticache_replication_group.redis.id
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
}

# 연결 수 알람
resource "aws_cloudwatch_metric_alarm" "redis_connections" {
  alarm_name          = "redis-connections-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "CurrConnections"
  namespace           = "AWS/ElastiCache"
  period              = 60
  statistic           = "Maximum"
  threshold           = 10000

  dimensions = {
    ReplicationGroupId = aws_elasticache_replication_group.redis.id
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
}
```

---

## 5. TIP

- **ElastiCache Serverless**: Redis 클러스터를 직접 관리 없이 사용. 자동 스케일링, 트래픽 없을 때 비용 최소화. 단 일반 클러스터 대비 latency가 약간 높을 수 있음
- **RESP3 프로토콜**: Redis 7+ + RESP3 지원 클라이언트 사용 시 Push 알림, 타입 정보 포함 등 개선된 통신 가능
- **ElastiCache for Valkey**: Redis 7.2 이후 AWS가 Valkey(Redis 오픈소스 fork)를 지원. 라이선스 이슈 없이 Redis 호환 유지
- **TTL 전략**: 모든 키에 TTL 설정 권장. TTL 없는 키가 누적되면 메모리 고갈. `SCAN` + `TTL` 명령으로 주기적으로 TTL 없는 키 탐지
