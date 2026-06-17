# Evidence-Gathering Plan: 120s+ Opportunity Requests

**Goal:** Identify the actual bottleneck for `GET …/opportunities` exceeding 120s.  
**Constraint:** No ADRs, migrations, refactors, or architecture work. Evidence only.  
**Window:** 24 hours from start.

---

## Instrumentation available today (no code changes)

### HTTP total time

| Source | Log line | Fields |
|--------|----------|--------|
| `api/main.py` | `[API] company_opportunities company_id={id} kind={kind} total={seconds}s` | Wall clock including full `discover_opportunities()` |
| `api/main.py` | `[API] arch_company_opportunities company_id={id} total={seconds}s` | Same for architecture |

### Discover total + breakdown (construction)

| Source | Log line | Fields |
|--------|----------|--------|
| `pipeline/opportunity_discovery.py` | `[OpportunityDiscovery] construction company={id} total {s}s … breakdown_fill={s}s` | `total`, `breakdown_fill`, `breakdown_items`, `final_matches`, `candidates_before_reduction` |
| `pipeline/opportunity_discovery.py` | `[OpportunityDiscovery] company={id} kind=construction db_phases_total={s}s cpu_phases_total={s}s` | Sum of DB-timed phases vs remainder (`SessionPhaseMetrics.log`) |

### DB phase timers (pool wait + queries bundled)

| Phase | Stored in | Printed separately? |
|-------|-----------|---------------------|
| Read bundle | `phase_metrics.read_ms` | No — only in `db_phases_total` aggregate |
| Hybrid session | `phase_metrics.hybrid_write_ms` | Yes — `hybrid_scoring {s}s` |
| Breakdown session | `phase_metrics.final_db_ms` | Yes — `breakdown_fill={s}s` |

### Hybrid scoring

| Source | Log line | Fields |
|--------|----------|--------|
| `opportunity_discovery.py` | `hybrid_scoring {s}s cache_hits=N freshly_scored=M` | Duration of hybrid **session** (includes pool checkout + `score_tender_pairs`) |
| Response JSON | `hybrid_scoring` object in API body | `cache_hits`, `freshly_scored`, `skipped_cap`, `api_errors` (client-visible) |
| `scripts/verify_company_opportunities_deploy.py` | stdout | `response_time`, `hybrid_cache_hits`, `hybrid_freshly_scored` |

### Per-stage CPU prints (not DB)

| Log substring | Measures |
|---------------|----------|
| `rule_scan {s}s candidates=N` | Rule scan only |
| `tender_items {s}s` | `_rule_tenders_to_opportunity_items` |
| `permit_scan {s}s` | Permit loop |
| `award_scan {s}s` | Award loop |

### Architecture path

| Source | Log line | Notes |
|--------|----------|-------|
| `opportunity_discovery.py` | `arch company={id} total {s}s` | No `breakdown_fill` (no breakdown attach phase) |
| Same hybrid / rule_scan / tender_items / permit_scan prints | | |

### Pool / DB errors (no timing)

| Source | Log line | Meaning |
|--------|----------|---------|
| `db/connection.py` | `[DB] get_session attempt … failed (retrying …)` | Retry storm on other routes using `get_session()` |
| Any | `QueuePool limit`, `timeout expired` | Pool exhaustion |
| `db/connection.py` | `[DB] Health check failed` | Postgres degraded |

### External scripts (no deploy)

| Script | Use |
|--------|-----|
| `scripts/verify_company_opportunities_deploy.py` | Single-request `response_time` vs production API |
| `scripts/verify_opportunities_concurrent.py` | 8 parallel discovers + health/permits probes |
| `scripts/capture_opportunities_baselines.py` | Local/staging timing (if env available) |

### Not instrumented today

| Metric | Gap |
|--------|-----|
| **DB pool wait** (isolated) | `session_scope()` has no checkout timing |
| **Query time** (without pool wait) | `read_ms` merges checkout + all read queries |
| **Hybrid CPU only** | `hybrid_write_ms` merges checkout + DB work inside hybrid |
| Railway CPU/memory | No process metrics in app logs |

---

## Minimum code changes (one small deploy)

**Single change:** add checkout timing to `session_scope()` in `db/connection.py` (~12 lines, env-gated).

```python
# At top: import os if not present
@contextmanager
def session_scope() -> Iterator[Session]:
    checkout_started = time.perf_counter()
    session = get_session_factory()()
    checkout_ms = (time.perf_counter() - checkout_started) * 1000
    if os.getenv("OPPORTUNITIES_DEBUG", "").lower() in {"1", "true", "yes"}:
        pool = get_engine().pool
        print(
            f"[DB:pool] checkout_ms={checkout_ms:.1f} "
            f"checked_out={pool.checkedout()} overflow={pool.overflow()} "
            f"size={pool.size()}"
        )
    try:
        yield session
    finally:
        session.close()
```

Add `import time` if missing.

**Env:** `OPPORTUNITIES_DEBUG=1` on Railway API service only.

**No other code required** for the five metrics if you accept:

| Metric | Source after this change |
|--------|--------------------------|
| Pool wait | Sum of 3× `[DB:pool] checkout_ms` per construction discover |
| Query execution (approx) | `read_ms - checkout_read_ms`; hybrid: `hybrid_write_ms - checkout_hybrid_ms` |
| Hybrid scoring | Existing `hybrid_scoring` line + `freshly_scored` |
| Breakdown | Existing `breakdown_fill` / `final_db_ms - checkout_breakdown_ms` |
| Total request | Existing `[API] … total=` |

Optional second line (only if Step 1 inconclusive): one JSON summary at end of `_discover_construction_opportunities` — **defer** until after Step 1–3.

---

## 24-hour evidence collection (fastest path)

### Hour 0–1: Zero deploy

```bash
# Production logs (Railway dashboard or CLI)
railway logs --since 24h | grep -E "OpportunityDiscovery|company_opportunities|QueuePool|timeout expired|\[DB\]"
```

Record for each slow window:

- `[API] … total=` ≥ 120
- Matching `construction company=… total` and `breakdown_fill=`
- `hybrid_scoring … freshly_scored=`
- `db_phases_total` vs `cpu_phases_total`
- Any `QueuePool` / `timeout expired` within ±30s

```bash
# Single-request client timing (no server logs)
python scripts/verify_company_opportunities_deploy.py
```

### Hour 1–2: Deploy pool instrumentation only

- Set `OPPORTUNITIES_DEBUG=1` on API service
- Redeploy (no behavior change)
- Trigger 3–5 discovers on known slow `company_id` (from logs or `1921`)

### Hour 2–4: SQL snapshot (read-only, during or after slow reports)

```sql
-- Connection pressure
SELECT COUNT(*) AS connections FROM pg_stat_activity WHERE datname = current_database();

-- tender_matches volume for slow company
SELECT COUNT(*) FROM tender_matches
WHERE company_kind = 'construction' AND company_id = 1921
  AND created_at >= NOW() - INTERVAL '192 hours';
```

Replace `1921` with IDs from logs.

### Hour 4–5: Concurrency test (off-peak, production or staging)

```bash
python scripts/verify_opportunities_concurrent.py https://YOUR-API.up.railway.app
```

Compare concurrent vs single `verify_company_opportunities_deploy.py` timing.

### Hour 5–24: Monitor + correlate

- Pull all `[DB:pool]` lines for requests with `[API] total` > 120
- Fill scoring table below
- Turn off `OPPORTUNITIES_DEBUG` after decision

---

## Ranked execution plan

### Step 1 — Mine existing production logs

| | |
|--|--|
| **Action** | Grep last 24–72h for `OpportunityDiscovery`, `company_opportunities`, `QueuePool` |
| **Implement** | **0 min** (no code) |
| **Expected evidence** | Slow requests show `breakdown_fill` &lt; 5s while `total` &gt; 120s → breakdown ruled out. `hybrid_scoring` seconds and `freshly_scored` counts. `db_phases_total` vs `cpu_phases_total`. Co-occurring pool errors. |
| **PASS (hypothesis supported)** | **Pool/concurrency:** `QueuePool`/`timeout expired` near slow `[API]` lines OR `db_phases_total` ≥ 60s with `cpu_phases_total` &lt; 30s and no high `freshly_scored`. **Hybrid:** `hybrid_scoring` ≥ 30s AND `freshly_scored` ≥ 10. **Query:** `db_phases_total` ≥ 90s, `cpu_phases_total` &lt; 20s, no pool errors. **CPU/Railway:** `cpu_phases_total` ≥ 80s, `db_phases_total` &lt; 40s. |
| **FAIL (hypothesis ruled out)** | Metric pattern opposite of above for that hypothesis. |

---

### Step 2 — Client-side single-request timing

| | |
|--|--|
| **Action** | Run `scripts/verify_company_opportunities_deploy.py` (or `curl -w '%{time_total}'`) 3× against production |
| **Implement** | **5 min** |
| **Expected evidence** | `response_time` vs server `[API] total=` (should match ±2s). If client ≥120s and logs show no matching slow server line → proxy/Vercel timeout path, not discover CPU. |
| **PASS** | Reproduces ≥120s with matching slow server log line. |
| **FAIL** | Cannot reproduce ≥120s in isolation → prioritize **H4 concurrent** or user-specific IDs. |

---

### Step 3 — Deploy `session_scope` pool checkout log

| | |
|--|--|
| **Action** | Minimal `db/connection.py` change + `OPPORTUNITIES_DEBUG=1` |
| **Implement** | **30–60 min** (PR + deploy) |
| **Expected evidence** | Per slow request: 3 `[DB:pool]` lines (construction). Σ `checkout_ms` vs `hybrid_scoring` / `breakdown_fill` / gap to `[API] total`. |
| **PASS (H1 pool wait)** | Σ `checkout_ms` ≥ **60s** OR any single ≥ **25s** on requests with `[API] total` ≥ 120s |
| **FAIL (H1)** | Σ `checkout_ms` &lt; **5s** on all slow requests |

---

### Step 4 — Correlate phase logs for one slow request

| | |
|--|--|
| **Action** | For one ≥120s request, extract full log sequence for same `company_id` |
| **Implement** | **15 min** (after Step 1 or 3) |
| **Expected evidence** | Table: `rule_scan`, `hybrid_scoring`, `tender_items`, `permit_scan`, `award_scan`, `breakdown_fill`, `db_phases_total`, `cpu_phases_total`, `[API] total` |
| **PASS (H3 hybrid)** | `hybrid_scoring` ≥ **30s** AND `freshly_scored` ≥ **10** AND Σ `checkout_ms` &lt; 10s |
| **FAIL (H3)** | `hybrid_scoring` &lt; **5s** OR `freshly_scored` ≤ **3** on slow requests |
| **PASS (H2 queries)** | (`read_ms` or `db_phases_total`) ≥ **60s** AND Σ `checkout_ms` &lt; **10s** AND `hybrid_scoring` &lt; **15s** |
| **FAIL (H2)** | `db_phases_total` &lt; **30s** on slow requests |
| **PASS (breakdown ruled out)** | `breakdown_fill` &lt; **3s** always when `total` &gt; 120s |
| **FAIL** | `breakdown_fill` ≥ **30s** (would implicate breakdown — unlikely post-optimization) |

---

### Step 5 — Concurrent discover test

| | |
|--|--|
| **Action** | `python scripts/verify_opportunities_concurrent.py $PROD_URL` then single `verify_company_opportunities_deploy.py` |
| **Implement** | **10 min** |
| **Expected evidence** | Concurrent paths ≥60s or ERR; single request faster; `[DB:pool] checked_out` ≥ 12 during burst |
| **PASS (H4 concurrent)** | Concurrent ≥2 failures or ≥60s AND single-request &lt; **40s** AND higher `checkout_ms` under burst |
| **FAIL (H4)** | Single request ≥120s while connections &lt; 10 and no overlapping discovers in logs |

---

### Step 6 — Postgres snapshot during slow period

| | |
|--|--|
| **Action** | Run connection count + `tender_matches` count SQL (above) when users report slowness or during Step 5 |
| **Implement** | **10 min** |
| **Expected evidence** | Connections near provider limit; large `tender_matches` row count correlates with high `read_ms` / `db_phases_total` |
| **PASS (H2)** | `tender_matches` count &gt; **150** for slow company AND high `db_phases_total` without high `checkout_ms` |
| **FAIL (H2)** | Small match history, still slow with low checkout → not read query volume |

---

### Step 7 — Railway metrics (no code)

| | |
|--|--|
| **Action** | Railway dashboard: CPU, memory, restarts for API service during Steps 2–5 |
| **Implement** | **5 min** |
| **Expected evidence** | CPU pegged during discover vs idle; OOM/restart events aligned with timeouts |
| **PASS (H5 saturation)** | `cpu_phases_total` ≥ **70%** of discover `total` AND Railway CPU ≥ **80%** sustained during slow requests |
| **FAIL (H5)** | Low CPU during slow requests; time in `db_phases_total` + `checkout_ms` explains ≥90% of `[API] total` |

---

## Decision worksheet (fill after Steps 1–7)

One row per slow request (`request_id` = timestamp + `company_id`):

| request_id | API_total_s | discover_total_s | Σ checkout_ms | db_phases_s | cpu_phases_s | hybrid_s | freshly_scored | breakdown_fill_s | QueuePool? |
|------------|-------------|------------------|---------------|-------------|--------------|----------|----------------|------------------|------------|

**Primary bottleneck** = largest column that explains gap to `API_total_s`:

1. If Σ `checkout_ms` ≥ 60s → **pool wait** (often with concurrency)
2. Else if `hybrid_s` ≥ 30s and `freshly_scored` ≥ 10 → **hybrid scoring**
3. Else if `db_phases_s` ≥ 60s and `checkout_ms` low → **query latency**
4. Else if `cpu_phases_s` ≥ 60s → **CPU / Railway saturation**
5. Else if only reproduces under Step 5 → **concurrent discovers**

---

## What not to do in this 24h window

- No ADR, migration, worker, or snapshot work
- No hybrid cap / pool size / query refactors until bottleneck column identified
- No optional JSON metrics deploy unless Steps 1–5 leave ambiguity between H2 and H1

---

## Revision history

| Version | Date | Change |
|---------|------|--------|
| 1.0 | 2026-06-15 | Initial evidence-only plan |
