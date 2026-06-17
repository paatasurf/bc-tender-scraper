# Production Debugging Plan: Opportunity Discovery &gt;120s

**Goal:** Collect evidence to determine which factor explains P95/P99 latency ≥120s on `GET /api/companies/{id}/opportunities` and `GET /api/arch-companies/{id}/opportunities`.

**Scope:** Evidence collection only. No architecture changes. No feature flags beyond short-lived debug logging.

**Hypotheses under test:**

| ID | Hypothesis |
|----|------------|
| **H1** | PostgreSQL connection pool wait |
| **H2** | PostgreSQL query latency (slow statements, large scans) |
| **H3** | Hybrid scoring (`score_tender_pairs` / construction uncapped fresh scores) |
| **H4** | Concurrent discover requests (pool contention + queueing) |
| **H5** | Railway resource saturation (CPU / memory / OOM throttle) |

**Prerequisites**

- Railway access: API service logs, Postgres metrics (or `psql` read-only), optional Metrics tab.
- Known slow `company_id` values from user reports or logs (capture at least 2 construction + 1 architecture).
- Staging mirror recommended for instrumentation deploy; production run uses **env-gated** logs (`OPPORTUNITIES_DEBUG=1`) for 24–48h max.

**Debug window:** Run H4 load test off-peak first in staging; production evidence = logs + SQL only unless approved change window.

---

## Shared instrumentation (deploy once)

Enable with environment variable: `OPPORTUNITIES_DEBUG=1` on `tenderscope-api` (Railway).

### A. `db/connection.py` — pool checkout timing

**Location:** `session_scope()` (lines ~291–298), wrap checkout.

```python
@contextmanager
def session_scope() -> Iterator[Session]:
    checkout_started = time.perf_counter()
    session = get_session_factory()()
    checkout_ms = (time.perf_counter() - checkout_started) * 1000
    if os.getenv("OPPORTUNITIES_DEBUG", "").lower() in {"1", "true", "yes"}:
        pool = get_engine().pool
        print(
            f"[DB:pool] session_scope checkout_ms={checkout_ms:.1f} "
            f"pool_size={pool.size()} checked_out={pool.checkedout()} "
            f"overflow={pool.overflow()} checked_in={pool.checkedin()}"
        )
    try:
        yield session
    finally:
        session.close()
```

**Exact log prefix:** `[DB:pool]`

**Note:** First DB call inside scope may add latency beyond checkout; checkout_ms captures pool wait + session creation.

### B. `pipeline/opportunity_discovery.py` — structured discover summary

**Location:** End of `_discover_construction_opportunities` (before `return`, ~line 1671) and `_discover_architecture_opportunities` (before `return`).

```python
if os.getenv("OPPORTUNITIES_DEBUG", "").lower() in {"1", "true", "yes"}:
    print(
        "[Discover:metrics] "
        + json.dumps(
            {
                "kind": "construction",  # or "architecture"
                "company_id": company.id,
                "total_ms": round(total_elapsed_ms, 1),
                "read_ms": round(phase_metrics.read_ms, 1),
                "hybrid_write_ms": round(phase_metrics.hybrid_write_ms, 1),
                "final_db_ms": round(phase_metrics.final_db_ms, 1),
                "cpu_ms": round(cpu_total_ms, 1),
                "tender_rows": len(bundle.tender_rows),
                "rule_candidates": len(rule_candidates),
                "permit_rows": len(bundle.permit_rows),
                "award_rows": len(bundle.award_rows),
                "fresh_cache_rows": len(fresh_cache),
                "hybrid_cache_hits": hybrid_scoring.get("cache_hits", 0),
                "hybrid_freshly_scored": hybrid_scoring.get("freshly_scored", 0),
                "hybrid_skipped_cap": hybrid_scoring.get("skipped_cap", 0),
                "final_matches": len(top),
                "breakdown_items": breakdown_count,  # construction only; 0 for arch
            },
            separators=(",", ":"),
        )
    )
```

Add `import json` and `import os` at module top if missing.

**Exact log prefix:** `[Discover:metrics]`

### C. `pipeline/ai_matching.py` — hybrid inner timing

**Location:** End of `score_tender_pairs()` before `return` (~line 513).

```python
if os.getenv("OPPORTUNITIES_DEBUG", "").lower() in {"1", "true", "yes"}:
    print(
        f"[Hybrid:score_tender_pairs] kind={kind} company_id={company_id} "
        f"fresh_cache_loaded={len(fresh_cache)} candidates={len(candidates)} "
        f"cache_hits={stats['cache_hits']} freshly_scored={stats['freshly_scored']} "
        f"skipped_cap={stats['skipped_cap']} api_errors={stats['api_errors']}"
    )
```

**Exact log prefix:** `[Hybrid:score_tender_pairs]`

### D. `api/main.py` — HTTP wall clock

**Location:** `company_opportunities` and `arch_company_opportunities` (existing `started` / `print` block ~471–474).

```python
if os.getenv("OPPORTUNITIES_DEBUG", "").lower() in {"1", "true", "yes"}:
    print(
        f"[API:opportunities] company_id={company_id} kind={kind} "
        f"http_total_ms={round((time.perf_counter() - started) * 1000, 1)}"
    )
```

**Exact log prefix:** `[API:opportunities]`

### E. Railway process snapshot (optional, H5)

**Location:** Start of `discover_opportunities()` (~line 1864).

```python
if os.getenv("OPPORTUNITIES_DEBUG", "").lower() in {"1", "true", "yes"}:
    try:
        import resource
        mem_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # Linux: KB→MB varies; on Linux /1024
        print(f"[Railway:process] discover_start maxrss_kb={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}")
    except Exception:
        pass
```

Pair with end-of-discover line:

```python
print(f"[Railway:process] discover_end company_id={company_id} total_ms={...}")
```

---

## Evidence collection without deploy (use first)

Existing production logs (no code change):

```bash
# Railway log search (adjust service name)
railway logs --filter "OpportunityDiscovery"
```

| Log substring | Field |
|---------------|-------|
| `construction company=NNN total` | `total` seconds, `breakdown_fill` |
| `hybrid_scoring` | `cache_hits`, `freshly_scored` |
| `db_phases_total` | Sum of instrumented DB phases |
| `cpu_phases_total` | CPU outside DB phases |
| `[API] company_opportunities` | HTTP total |

**Grep pool exhaustion:**

```bash
railway logs --filter "QueuePool"
railway logs --filter "timeout expired"
railway logs --filter "pool"
```

If `QueuePool` or `timeout expired` co-occur with slow `[API] company_opportunities` timestamps → **H1/H4 supportive**.

---

---

## H1: PostgreSQL connection pool wait

### Theory

`session_scope()` checkout blocks up to **30s** (`pool_timeout` default) when 15 connections (5+10 overflow) are checked out. Three phases per discover × concurrent requests → wall time dominated by wait, not CPU.

### Code instrumentation

| File | What |
|------|------|
| `db/connection.py` | Shared **A** — `[DB:pool] session_scope checkout_ms=...` |
| `pipeline/opportunity_discovery.py` | Shared **B** — compare `read_ms + hybrid_write_ms + final_db_ms` vs `total_ms` |

### Exact log statements to collect

```
[DB:pool] session_scope checkout_ms=... pool_size=... checked_out=... overflow=...
[Discover:metrics] {"total_ms":...,"read_ms":...,"hybrid_write_ms":...,"final_db_ms":...}
[API:opportunities] http_total_ms=...
```

**Per slow request, record 3× `[DB:pool]` lines** (read, hybrid, breakdown sessions for construction).

### SQL queries (run on production Postgres read-only)

**Active connections by application state:**

```sql
SELECT state, wait_event_type, wait_event, COUNT(*) AS cnt
FROM pg_stat_activity
WHERE datname = current_database()
GROUP BY 1, 2, 3
ORDER BY cnt DESC;
```

**Connections near limit (Railway often max ~97–100):**

```sql
SELECT COUNT(*) AS total_connections
FROM pg_stat_activity
WHERE datname = current_database();
```

**Long-running queries during incident (run while slow discovers reported):**

```sql
SELECT pid,
       now() - query_start AS duration,
       state,
       wait_event_type,
       wait_event,
       LEFT(query, 120) AS query_preview
FROM pg_stat_activity
WHERE datname = current_database()
  AND state != 'idle'
  AND query NOT LIKE '%pg_stat_activity%'
ORDER BY duration DESC
LIMIT 20;
```

### Expected results

| Scenario | `checkout_ms` | `checked_out` | `pg_stat_activity` total |
|----------|---------------|---------------|---------------------------|
| **H1 confirmed** | ≥10s on one or more phases; sum ≥30s common | Often 14–15 at peak | `total_connections` near max; `wait_event` ClientRead / IO |
| **H1 rejected** | &lt;500ms all phases | &lt;8 typical | Connections well below max during slowness |

**Gap test:** If `http_total_ms` ≫ `total_ms` + 5000 → wait **outside** discover (proxy, client) not H1.

### Pass / fail criteria

| Result | Criteria |
|--------|----------|
| **PASS (H1 is root cause)** | On ≥3 slow requests (&gt;120s): Σ `checkout_ms` ≥ **60s** OR any single `checkout_ms` ≥ **25s** AND `QueuePool` / `timeout expired` in logs within ±5s |
| **FAIL (H1 not root cause)** | Σ `checkout_ms` &lt; **5s** on all slow requests AND no pool errors AND `total_ms` from `[Discover:metrics]` ≥ **100s** |

---

## H2: PostgreSQL query latency

### Theory

Individual statements (tender load, permit scan, award client scan, `tender_matches` wide SELECT) take seconds each; sum of phase DB time explains &gt;120s **without** large pool wait.

### Code instrumentation

| File | What |
|------|------|
| `pipeline/opportunity_discovery.py` | Shared **B** — high `read_ms` or `hybrid_write_ms` with low `checkout_ms` |
| Optional sub-spans inside `_load_construction_read_bundle` | One-off debug branch |

**Optional sub-span logs** (inside `_load_construction_read_bundle`, `OPPORTUNITIES_DEBUG=1`):

```python
t0 = time.perf_counter()
tender_rows = _load_tender_candidates(session, "construction", max_candidates)
print(f"[DB:query_span] load_tenders_ms={round((time.perf_counter()-t0)*1000,1)} count={len(tender_rows)}")
# Repeat for permit_rows, award_rows, load_fresh_company_tender_matches
```

**Exact log prefix:** `[DB:query_span]`

### Exact log statements

```
[DB:query_span] load_tenders_ms=... count=800
[DB:query_span] load_permits_ms=... count=...
[DB:query_span] load_awards_ms=... count=...
[DB:query_span] load_fresh_matches_ms=... count=...
[Discover:metrics] read_ms=...
```

### SQL queries

**Table sizes (context):**

```sql
SELECT relname AS table_name,
       n_live_tup AS est_rows
FROM pg_stat_user_tables
WHERE relname IN (
  'tenders', 'commercial_tenders', 'arch_tenders',
  'permits', 'contract_awards', 'tender_matches', 'companies'
)
ORDER BY n_live_tup DESC;
```

**`tender_matches` row count for slow company (replace IDs):**

```sql
SELECT company_kind, company_id, COUNT(*) AS row_count
FROM tender_matches
WHERE company_id = :company_id
  AND company_kind = 'construction'
  AND created_at >= NOW() - INTERVAL '192 hours'
GROUP BY 1, 2;
```

**EXPLAIN (ANALYZE, BUFFERS) — run on staging replica or off-peak production** (construction read path):

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM tenders ORDER BY id DESC LIMIT 400;

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM commercial_tenders ORDER BY id DESC LIMIT 400;

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM tender_matches
WHERE company_kind = 'construction'
  AND company_id = :company_id
  AND created_at >= NOW() - INTERVAL '192 hours'
ORDER BY score DESC, id DESC;

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM contract_awards
WHERE buyer_organization != ''
ORDER BY award_date DESC, id DESC
LIMIT 800;
```

**Slow query stats (if `pg_stat_statements` enabled):**

```sql
SELECT calls,
       round(mean_exec_time::numeric, 2) AS mean_ms,
       round(max_exec_time::numeric, 2) AS max_ms,
       LEFT(query, 100) AS query_preview
FROM pg_stat_statements
WHERE query ILIKE '%tender_matches%'
   OR query ILIKE '%contract_awards%'
   OR query ILIKE '%permits%'
ORDER BY mean_exec_time DESC
LIMIT 15;
```

### Expected results

| Scenario | `read_ms` | EXPLAIN | `load_fresh_matches_ms` |
|----------|-----------|---------|-------------------------|
| **H2 confirmed** | ≥30s on slow requests | Seq Scan &gt;2s on permits/awards/tender_matches | &gt;5s when row_count &gt;200 |
| **H2 rejected** | &lt;10s | All analyzed queries &lt;500ms | Low row counts |

### Pass / fail criteria

| Result | Criteria |
|--------|----------|
| **PASS (H2 is root cause)** | `read_ms + hybrid_write_ms + final_db_ms` ≥ **90s** on slow requests AND Σ `checkout_ms` &lt; **10s** AND at least one EXPLAIN (ANALYZE) or `pg_stat_statements` mean &gt; **2000ms** for a discover-related query |
| **FAIL (H2 not root cause)** | Phase DB sums &lt; **30s** on slow requests OR slowness remains with `total_ms` high but DB sums low (→ CPU H3 or H5) |

---

## H3: Hybrid scoring

### Theory

`score_tender_pairs` runs up to **20** fresh deterministic scores for construction (no `inline_cap`), each with `_load_tender_row` + upsert; dominates `hybrid_write_ms` when `breakdown_json` cache cold.

### Code instrumentation

| File | What |
|------|------|
| `pipeline/ai_matching.py` | Shared **C** — `[Hybrid:score_tender_pairs]` |
| `pipeline/opportunity_discovery.py` | Existing print: `hybrid_scoring ... freshly_scored=...` + Shared **B** |

### Exact log statements

```
[OpportunityDiscovery] construction company=NNN hybrid_scoring X.XXs cache_hits=N freshly_scored=M
[Hybrid:score_tender_pairs] kind=construction company_id=NNN fresh_cache_loaded=F candidates=20 cache_hits=... freshly_scored=... skipped_cap=0
[Discover:metrics] "hybrid_write_ms":...,"hybrid_freshly_scored":...
```

### SQL queries

**Cache warmth for company:**

```sql
SELECT COUNT(*) FILTER (WHERE breakdown_json IS NOT NULL) AS with_breakdown,
       COUNT(*) AS total,
       MAX(created_at) AS latest_match
FROM tender_matches
WHERE company_kind = 'construction'
  AND company_id = :company_id
  AND created_at >= NOW() - INTERVAL '168 hours';
```

**Recent upsert rate (hybrid write storm):**

```sql
SELECT company_id, COUNT(*) AS upserts_last_hour
FROM tender_matches
WHERE company_kind = 'construction'
  AND created_at >= NOW() - INTERVAL '1 hour'
GROUP BY company_id
ORDER BY upserts_last_hour DESC
LIMIT 20;
```

### Controlled experiment (staging only)

1. **Cold cache:** `DELETE FROM tender_matches WHERE company_id = :id AND company_kind = 'construction';`
2. Single `GET /api/companies/:id/opportunities`
3. Record `hybrid_write_ms`, `freshly_scored`
4. Repeat same GET within 60s
5. Compare run 1 vs run 2

### Expected results

| Run | `freshly_scored` | `hybrid_write_ms` | `total_ms` |
|-----|------------------|-------------------|------------|
| Cold (H3) | 15–20 | 5–30s+ | Often &gt;30s |
| Warm | 0–2 | &lt;2s | Drops sharply |

Construction: `skipped_cap` should stay **0** (cap not applied).

### Pass / fail criteria

| Result | Criteria |
|--------|----------|
| **PASS (H3 is root cause)** | On ≥3 production slow requests: `hybrid_freshly_scored` ≥ **10** AND `hybrid_write_ms` ≥ **30s** AND `read_ms` &lt; **15s** AND pool checkout sum &lt; **10s** |
| **FAIL (H3 not root cause)** | Slow requests have `freshly_scored` ≤ **3** OR `hybrid_write_ms` &lt; **5s** OR cold/warm staging experiment shows &lt;2× delta |

---

## H4: Concurrent discover requests

### Theory

Multiple simultaneous discovers exhaust the pool; each request waits on checkout; unrelated endpoints slow. Single isolated request is fast; concurrency triggers &gt;120s.

### Code instrumentation

| File | What |
|------|------|
| `db/connection.py` | Shared **A** — correlate `checked_out` spikes |
| `api/main.py` | Shared **D** — overlapping `[API:opportunities]` timestamps |

### Load test (staging → production off-peak with approval)

Use existing script:

```bash
python scripts/verify_opportunities_concurrent.py https://YOUR-API.up.railway.app
```

Extend concurrency evidence — run with overlapping discovers:

```bash
# 8 parallel discovers (script default paths)
python scripts/verify_opportunities_concurrent.py "$API_BASE"

# Immediately probe lightweight endpoints
curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" "$API_BASE/api/health"
curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" "$API_BASE/api/permits?limit=10"
```

**Record:** per-path status, elapsed; any `ERR:timeout` or status ≠ 200.

### Exact log statements

Collect **time-aligned** log slice (±2 min):

```
[API:opportunities] ... http_total_ms=...
[DB:pool] ... checked_out=...
[DB] get_session attempt ...  (if other routes use get_session with retry)
QueuePool limit ... timeout ...
```

Count overlapping discovers:

```bash
# Count opportunity API log lines in a 60s window
railway logs --since 5m | grep -c "company_opportunities"
```

### SQL queries (during load test)

Run every 10s during test:

```sql
SELECT COUNT(*) AS active,
       COUNT(*) FILTER (WHERE wait_event IS NOT NULL) AS waiting
FROM pg_stat_activity
WHERE datname = current_database()
  AND state = 'active';
```

### Expected results

| Scenario | Concurrent test | Single request | Probes |
|----------|-----------------|----------------|--------|
| **H4 confirmed** | Several paths &gt;60s or timeout | Same company &lt;30s alone | `/api/permits` &gt;5s or fails during burst |
| **H4 rejected** | All concurrent &lt;30s | Slow even when only 1 user | Probes stay &lt;2s during burst |

### Pass / fail criteria

| Result | Criteria |
|--------|----------|
| **PASS (H4 is root cause)** | Concurrent script: ≥2 discover paths **≥60s** OR failures AND single-company off-peak request **&lt;40s** AND `checked_out` ≥ **12** during burst |
| **FAIL (H4 not root cause)** | Single isolated slow request ≥120s while `pg_stat_activity` connections &lt; **10** AND no concurrent discovers in logs |

---

## H5: Railway resource saturation

### Theory

API container CPU throttled or memory pressure causes slow Python execution across **all** phases (`cpu_ms` high, DB sums moderate); may coincide with daily pipeline subprocess.

### Code instrumentation

| Source | What |
|--------|------|
| Railway dashboard | CPU %, memory %, restarts, OOM |
| Shared **B** | `cpu_ms` vs DB phase sums |
| Shared **E** | `[Railway:process]` maxrss |
| `pipeline/executor.py` | Log `pipeline_status` during incident |

### Exact log statements

```
[Discover:metrics] "cpu_ms":...,"read_ms":...,"hybrid_write_ms":...
[OpportunityDiscovery] cpu_phases_total=...
[Railway:process] discover_start/discover_end maxrss_kb=...
[Pipeline] ...  (subprocess start during scrape window)
```

**Railway CLI (no code):**

```bash
railway status
# Metrics UI: CPU, Memory for API service — screenshot at incident time
```

### SQL queries

**Not primary for H5.** Optional: confirm DB not saturated while app is slow:

```sql
SELECT NOW() AS ts,
       COUNT(*) FILTER (WHERE state = 'active') AS active_queries
FROM pg_stat_activity
WHERE datname = current_database();
```

If `active_queries` ≤ 3 but `cpu_ms` ≥ 60s → supports H5 over H2.

### System probes during slow period

From Railway shell or one-off debug container on same service:

```bash
# If shell available
ps aux | head
free -m
```

If no shell: rely on Railway Metrics **CPU &gt;80% sustained** during discover window.

### Expected results

| Scenario | `cpu_ms` | DB sums | Railway CPU | Pipeline |
|----------|----------|---------|-------------|----------|
| **H5 confirmed** | ≥60s | &lt;40s | &gt;80% sustained | Overlap with `pipeline running` |
| **H5 rejected** | &lt;15s | or DB sums explain total | CPU &lt;50% | No correlation |

### Pass / fail criteria

| Result | Criteria |
|--------|----------|
| **PASS (H5 is root cause)** | ≥3 slow requests: `cpu_ms` ≥ **70%** of `total_ms` AND (`read_ms`+`hybrid_write_ms`+`final_db_ms`) &lt; **40%** of `total_ms` AND Railway CPU ≥ **80%** during window OR OOM/restart event correlated |
| **FAIL (H5 not root cause)** | `cpu_ms` &lt; **20s** on slow requests OR DB/checkout sums account for ≥ **70%** of `total_ms` |

---

## Decision matrix (after evidence collection)

Fill with measured values:

| Request ID | `http_total_ms` | Σ `checkout_ms` | `read_ms` | `hybrid_write_ms` | `final_db_ms` | `cpu_ms` | `freshly_scored` | concurrent? |
|------------|-----------------|-----------------|-----------|-------------------|---------------|----------|------------------|-------------|
| slow-1 | | | | | | | | |
| slow-2 | | | | | | | | |
| fast-baseline | | | | | | | | |

**Assign root cause:**

1. If **H1 PASS** → pool wait (often with **H4**).
2. Else if **H2 PASS** → query latency.
3. Else if **H3 PASS** → hybrid scoring.
4. Else if **H4 PASS** → concurrency (may duplicate H1).
5. Else if **H5 PASS** → Railway saturation.
6. If multiple PASS → **primary** = largest contributor in table (max of checkout sum, DB sums, `hybrid_write_ms`, `cpu_ms`).

---

## Execution schedule (minimal production risk)

| Day | Activity | Hypotheses |
|-----|----------|------------|
| 1 | Grep existing logs; SQL table sizes + slow company `tender_matches` count | H2, H3 |
| 1 | `pg_stat_activity` during reported slow window (if live) | H1, H4 |
| 2 | Deploy `OPPORTUNITIES_DEBUG=1` to staging; validate log format | All |
| 2 | Staging cold/warm hybrid test | H3 |
| 3 | Staging `verify_opportunities_concurrent.py` | H4 |
| 3–4 | Production debug logs 24h (low traffic window) | H1, H2, H3, H5 |
| 4 | Production concurrent test (off-peak, approved) | H4 |
| 5 | Fill decision matrix; disable `OPPORTUNITIES_DEBUG` | — |

---

## Log collection commands (Railway)

```bash
# Slow discovers
railway logs --since 2h | grep -E "Discover:metrics|DB:pool|Hybrid:score|API:opportunities|QueuePool"

# Export to file for analysis
railway logs --since 24h > /tmp/opportunities-debug.log
grep "Discover:metrics" /tmp/opportunities-debug.log | jq -r . 2>/dev/null || grep "Discover:metrics" /tmp/opportunities-debug.log
```

Parse `[Discover:metrics]` JSON with `jq` after stripping prefix:

```bash
grep '\[Discover:metrics\]' /tmp/opportunities-debug.log | sed 's/.*\[Discover:metrics\] //' | jq -s 'map(select(.total_ms > 120000))'
```

(Adjust if `total_ms` logged in seconds in legacy prints — unified metrics use ms.)

---

## Related documents

- [root_cause_analysis.md](./root_cause_analysis.md)

---

## Revision history

| Version | Date | Change |
|---------|------|--------|
| 1.0 | 2026-06-15 | Initial production debug plan |
