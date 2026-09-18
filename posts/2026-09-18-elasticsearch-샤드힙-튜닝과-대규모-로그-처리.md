# Elasticsearch 샤드·힙 튜닝과 대규모 로그 처리

## 개요

로그 수집 파이프라인을 구성하다 보면 어느 순간 Elasticsearch가 병목이 되는 시점이 온다. 초당 수만 건의 로그를 인덱싱하면서 동시에 검색과 집계 쿼리를 처리해야 하는 상황, 힙이 부족해 GC가 폭발하거나 샤드 수가 너무 많아 클러스터 상태가 불안정해지는 경험은 대부분의 팀이 한 번씩 겪게 된다.

이 글에서는 Elasticsearch 운영에서 가장 영향도가 높은 두 가지 축인 **샤드 설계**와 **JVM 힙 튜닝**을 중심으로, 대규모 로그 처리 환경에서 실무에 바로 적용할 수 있는 설정과 전략을 다룬다. 버전은 Elasticsearch 8.x 기준이지만 핵심 개념은 7.x 환경에도 대부분 적용 가능하다.

---

## 핵심 개념

### 샤드란 무엇이고 왜 중요한가

Elasticsearch의 인덱스는 하나 이상의 **샤드(shard)**로 분산 저장된다. 샤드는 Lucene 인덱스 하나이며, 프라이머리 샤드와 레플리카 샤드로 구분된다. 샤드 수는 인덱스 생성 시점에 결정되며 이후 변경이 어렵기 때문에 초기 설계가 중요하다.

**샤드 수가 너무 많을 때의 문제:**
- 마스터 노드의 클러스터 상태(cluster state) 크기 증가 → 마스터 부하 상승
- 각 샤드는 힙 메모리를 소비 (세그먼트 메타데이터, 필드 데이터 등)
- 검색 시 모든 샤드에 fan-out 쿼리가 발생 → 오버헤드 증가

**샤드 수가 너무 적을 때의 문제:**
- 단일 샤드에 데이터가 집중되어 노드 간 부하 분산 불가
- 병렬 처리 이점 손실

일반적인 권장 기준은 **샤드 하나당 10~50GB** 수준이다. 그러나 로그 처리 환경에서는 시계열 데이터 특성상 ILM(Index Lifecycle Management)과 결합한 롤오버 전략이 더 중요하다.

### JVM 힙과 GC의 관계

Elasticsearch는 JVM 위에서 동작하며, 힙 메모리 설정이 성능에 직접적인 영향을 미친다. 중요한 규칙 두 가지:

1. **힙은 물리 메모리의 50%를 넘지 않는다**: 나머지 절반은 OS 파일 시스템 캐시(page cache)에 할당해야 Lucene의 I/O 성능을 유지할 수 있다.
2. **힙은 32GB를 넘지 않는다**: JVM의 Compressed OOP(Ordinary Object Pointer)가 32GB를 초과하면 비활성화되어 메모리 효율이 급격히 떨어진다.

---

## 실전 예제

### 1. ILM 기반 롤오버 인덱스 설계

로그 처리에서는 날짜 기반 인덱스(`logs-2024.01.01`)보다 **롤오버(Rollover)** 방식이 더 유연하다.

```json
// ILM 정책 생성
PUT _ilm/policy/logs-policy
{
  "policy": {
    "phases": {
      "hot": {
        "min_age": "0ms",
        "actions": {
          "rollover": {
            "max_primary_shard_size": "30gb",
            "max_age": "1d"
          },
          "set_priority": {
            "priority": 100
          }
        }
      },
      "warm": {
        "min_age": "2d",
        "actions": {
          "forcemerge": {
            "max_num_segments": 1
          },
          "shrink": {
            "number_of_shards": 1
          },
          "set_priority": {
            "priority": 50
          }
        }
      },
      "cold": {
        "min_age": "7d",
        "actions": {
          "freeze": {},
          "set_priority": {
            "priority": 0
          }
        }
      },
      "delete": {
        "min_age": "30d",
        "actions": {
          "delete": {}
        }
      }
    }
  }
}
```

```json
// 인덱스 템플릿 설정 (샤드 수 포함)
PUT _index_template/logs-template
{
  "index_patterns": ["logs-*"],
  "data_stream": {},
  "template": {
    "settings": {
      "number_of_shards": 3,
      "number_of_replicas": 1,
      "index.lifecycle.name": "logs-policy",
      "index.lifecycle.rollover_alias": "logs",
      "refresh_interval": "10s",
      "translog.durability": "async",
      "translog.sync_interval": "30s"
    },
    "mappings": {
      "dynamic": "strict",
      "properties": {
        "@timestamp": { "type": "date" },
        "level": { "type": "keyword" },
        "service": { "type": "keyword" },
        "message": {
          "type": "text",
          "norms": false
        },
        "trace_id": { "type": "keyword" },
        "duration_ms": { "type": "long" }
      }
    }
  }
}
```

위 설정에서 주목할 부분:
- `refresh_interval: 10s`: 기본값 1초보다 길게 설정하여 세그먼트 생성 빈도를 줄이고 인덱싱 처리량을 높인다.
- `translog.durability: async`: 매 요청마다 fsync하지 않고 주기적으로 flush. 로그 처리 특성상 약간의 데이터 유실 허용 범위 내에서 쓰기 성능을 크게 향상시킨다.
- `dynamic: strict`: 예상치 못한 필드가 인덱싱되어 매핑 폭발(mapping explosion)이 발생하는 것을 방지한다.

### 2. JVM 힙 및 GC 튜닝

`jvm.options` 파일 설정:

```bash
# /etc/elasticsearch/jvm.options.d/heap.options

# 노드 물리 메모리 64GB 기준 → 힙 30GB 설정 (32GB 미만 유지)
-Xms30g
-Xmx30g

# G1GC 사용 (Elasticsearch 7.x 이상 기본)
-XX:+UseG1GC
-XX:G1HeapRegionSize=4m
-XX:InitiatingHeapOccupancyPercent=30

# GC 로그 설정
-Xlog:gc*,gc+age=trace,safepoint:file=/var/log/elasticsearch/gc.log:utctime,pid,tags:filecount=32,filesize=64m
```

`InitiatingHeapOccupancyPercent=30`은 힙 사용률 30%에서 G1GC의 백그라운드 마킹을 시작한다는 의미다. 기본값(45%)보다 낮게 설정하면 Full GC 발생 가능성을 줄일 수 있지만, GC 빈도는 증가한다. 인덱싱 부하가 높은 hot 노드에서는 이 값을 낮추는 것이 유리하다.

### 3. Logstash/Beats 설정 최적화

Elasticsearch로 데이터를 보내는 클라이언트 쪽 설정도 중요하다.

```yaml
# logstash.conf 출력 설정
output {
  elasticsearch {
    hosts => ["https://es-node1:9200", "https://es-node2:9200", "https://es-node3:9200"]
    index => "logs"
    action => "create"
    
    # 벌크 요청 설정
    flush_size => 5000
    idle_flush_time => 5
    
    # 재시도 설정
    retry_max_interval => 64
    retry_initial_interval => 2
    
    # HTTP 압축 (네트워크 부하 감소)
    http_compression => true
    
    # 파이프라인 병렬화
    pipeline_workers => 4
    pipeline_batch_size => 2000
  }
}
```

### 4. 클러스터 상태 모니터링 쿼리

운영 중 클러스터 상태를 빠르게 진단하기 위한 API:

```bash
# 샤드 분포 확인
GET _cat/shards?v&s=store:desc

# 노드별 힙 사용률 확인
GET _cat/nodes?v&h=name,heap.current,heap.max,heap.percent,ram.percent,cpu

# 느린 인덱싱 쿼리 확인 (인덱스별 통계)
GET _cat/indices?v&h=index,docs.count,store.size,indexing.index_total,indexing.index_time&s=indexing.index_time:desc

# 세그먼트 현황 확인
GET _cat/segments?v&h=index,shard,segment,size,committed,searchable

# 핫 스레드 확인 (부하 원인 분석)
GET _nodes/hot_threads?threads=5&interval=500ms
```

### 5. 샤드 재배치 및 강제 병합

```bash
# warm 단계에서 수동 force merge (세그먼트 통합)
POST logs-000001/_forcemerge?max_num_segments=1&only_expunge_deletes=false

# 특정 노드로 샤드 이동 (데이터 티어 구성 시)
PUT _cluster/settings
{
  "persistent": {
    "cluster.routing.allocation.require._tier_preference": "data_hot,data_warm,data_cold"
  }
}

# 샤드 재배치 속도 조정 (대량 재인덱싱 시)
PUT _cluster/settings
{
  "transient": {
    "cluster.routing.allocation.node_concurrent_recoveries": 4,
    "indices.recovery.max_bytes_per_sec": "200mb"
  }
}
```

---

## 주의사항 및 트레이드오프

### 샤드 수 결정의 어려움

"샤드 하나당 X GB"라는 가이드라인은 출발점일 뿐이다. 실제 최적값은 **문서 수**, **필드 수**, **쿼리 패턴**, **집계 복잡도**에 따라 달라진다. 특히 로그 데이터에서 `terms` 집계를 많이 사용한다면 필드 데이터 캐시로 인해 예상보다 힙을 많이 소비할 수 있다.

**트레이드오프 정리:**

| 설정 | 장점 | 단점 |
|------|------|------|
| 샤드 수 증가 | 병렬 처리 향상 | 마스터 부하, 힙 소비 증가 |
| refresh_interval 증가 | 인덱싱 처리량 향상 | 검색 지연(near-real-time 저하) |
| translog async | 쓰기 성능 향상 | 노드 비정상 종료 시 최대 sync_interval 분량 손실 |
| forcemerge | 검색 성능 향상, 디스크 절약 | 수행 중 I/O/CPU 부하 급증 |
| 레플리카 증가 | 읽기 처리량 향상, 가용성 향상 | 디스크 사용량 및 인덱싱 부하 증가 |

### 힙 설정의 함정

힙을 무조건 크게 잡는 것은 금물이다. 힙이 커질수록 Full GC 발생 시 STW(Stop-The-World) 시간도 길어진다. 32GB 제한 이상으로 힙을 설정하면 오히려 성능이 저하되는 역설이 발생한다.

또한, **fielddata 캐시**를 명시적으로 제한하지 않으면 집계 쿼리가 힙을 모두 소진시킬 수 있다:

```yaml
# elasticsearch.yml
indices.fielddata.cache.size: 20%
indices.breaker.fielddata.limit: 40%
indices.breaker.request.limit: 40%
indices.breaker.total.limit: 70%
```

### 매핑 폭발 방지

동적 매핑을 허용한 상태로 비정형 로그를 그대로 넣으면, 필드 수가 수백~수천 개로 증가하며 클러스터 상태가 거대해진다. 이는 마스터 노드의 메모리 부족으로 이어진다. `dynamic: strict` 또는 `dynamic: false`를 기본으로 설정하고 필요한 필드만 명시적으로 매핑해야 한다.

---

## 정리

Elasticsearch로 대규모 로그를 처리할 때 가장 중요한 설계 원칙을 정리하면 다음과 같다.

**샤드 설계:**
- ILM + 롤오버를 조합해 샤드 크기를 10~30GB 범위로 유지한다
- hot/warm/cold 티어로 데이터를 분리하여 비용과 성능을 균형 있게 관리한다
- 매핑 폭발을 방지하기 위해 `dynamic: strict`를 적용하고 스키마를 사전에 설계한다

**힙 튜닝:**
- 힙은 물리 메모리의 50%, 최대 30GB로 설정한다
- G1GC를 사용하고 `InitiatingHeapOccupancyPercent`를 워크로드에 맞게 조정한다
- fielddata/request breaker를 반드시 설정하여 OOM을 방지한다

**인덱싱 최적화:**
- `refresh_interval`을 5~30초로 늘려 세그먼트 생성 빈도를 낮춘다
- 벌크 요청 크기를 5,000~10,000건 수준으로 조정한다
- warm 단계에서 forcemerge를 실행하여 세그먼트를 통합하고 검색 성능을 향상시킨다

튜닝은 한 번의 설정으로 끝나지 않는다. 운영 중 `_cat/nodes`, `_nodes/stats`, `_nodes/hot_threads`를 정기적으로 모니터링하고, GC 로그를 분석하여 점진적으로 개선하는 것이 현실적인 접근법이다. 설정 변경 후에는 반드시 스테이징 환경에서 부하 테스트를 거쳐 프로덕션에 적용하는 것을 권장한다.
