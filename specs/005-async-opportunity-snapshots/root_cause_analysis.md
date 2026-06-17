# Root Cause Analysis: Opportunity Discovery Timeouts (&gt;120s)

**Feature:** `005-async-opportunity-snapshots`  
**Date:** 2026-06-15  
**Symptom:** `GET /api/companies/{id}/opportunities` (and arch equivalent) still exceeds **120 seconds** after **breakdown optimization** (final-phase breakdown attach limited to assembled `top` items only).

**Conclusion (executive):** Breakdown attach was never the dominant cost for typical companies. Total discover time is driven by **(1) connection pool queuing under concurrency**, **(2) construction hybrid scoring without inline cap (up to 20 DB-backed scores per request)**, **(3) heavy permit/award candidate queries**, **(4) repeated `tender_matches` loads**, and **(5) large rule-candidate loops**. Optimizing breakdown on ≤15 items cannot reduce a 180–300s pipeline to &lt;120s when those stages account for 90%+ of wall time.

---

## 1. Current `discover_opportunities` execution path

### Entry

```
GET /api/companies/{id}/opportunities  (api/main.py)
  → discover_opportunities(company_id, kind, min_score, limit, max_candidates=400)
    → _discover_construction_opportunities  OR  _discover_architecture_opportunities
```

No Redis, no external cache, no background job—**full pipeline runs in the HTTP worker process**.

### Phased sessions (spec 004)

Each discover uses **multiple short `session_scope()` blocks** (connection returned between CPU phases):

| Phase | Session? | Work |
|-------|----------|------|
| B1 Read | Yes | Load company, tenders, permits, awards, `tender_matches` |
| B2 CPU | No | Rule scan, tender_items, permit/award loops, assembly |
| B3 Hybrid | Yes | `score_tender_pairs` + reload `tender_matches` |
| B4 CPU | No | `_rule_tenders_to_opportunity_items` (if not merged in construction) |
| B5 Breakdown | Yes (construction only) | `_attach_final_construction_tender_breakdowns` on `top` |

**Wall time** = sum(phase durations) + **pool wait** on each `session_scope()` checkout.

### Complexity summary

```
T_total ≈ T_pool_wait + T_read + T_rule_scan + T_hybrid + T_tender_items
          + T_permits + T_awards + T_assemble + T_breakdown
```

Breakdown optimization reduced **T_breakdown** only. If `T_breakdown` was ~5–30s before and now ~0.1–2s, **T_total barely changes** when `T_hybrid + T_pool_wait + T_read` dominate.

---

## 2. Construction path (`_discover_construction_opportunities`)

### B1 Read bundle (`_load_construction_read_bundle`)

| Query / work | Approx rows | Notes |
|--------------|-------------|-------|
| `session.get(Company)` | 1 | |
| `_load_tender_candidates(construction, 400)` | **800** | 400 `Tender` + 400 `CommercialTender` (`ORDER BY id DESC LIMIT 400` each) |
| `_load_permit_candidates(signals, 200)` | up to **200** | See §6 — may scan 300+ rows internally |
| `_load_award_candidates(company, 200)` | up to **400** | See §6 — multiple queries + wide scan |
| `load_fresh_company_tender_matches` | **all fresh rows** | 168h window; Python filter; can be 50–500+ rows |

**Expected complexity:** O(800 + 200 + 400 + |fresh_cache|) DB rows loaded; **3–8s** DB under load; higher if pool wait.

### B2 Rule scan (`_scan_construction_rule_tenders_from_rows`)

- Iterates **every** loaded tender row (~800).
- Skips closed tenders; **every open tender** becomes a `RuleTenderCandidate` (not pre-filtered by score).
- Typical **open rule_candidates: 300–700**.

**Expected complexity:** O(tender_rows) CPU — **1–4s** typical.

### B3 Hybrid (`_run_hybrid_tender_scoring` → `score_tender_pairs`)

- Sort rule_candidates; take **top 20** (`HYBRID_AI_CANDIDATE_LIMIT`).
- For each of 20 candidates:
  - Cache hit if `breakdown_json` present in `fresh_cache` (loaded again inside `score_tender_pairs`).
  - Else: `_load_tender_row` (1 query per miss), `score_construction_match`, `_upsert_tender_match`.
- **Critical:** `inline_cap` is **ignored for construction** (`kind != "construction"` check in `score_tender_pairs` line 470–472).
  - Architecture: max **5** fresh scores per request.
  - Construction: up to **20** fresh scores + commits per request if cache cold.

After hybrid: **second** `load_fresh_company_tender_matches` to build `fresh_cache` dict.

**Expected complexity:** O(20) DB round-trips worst case; **2–15s** with cold cache; **0.5–3s** warm.

### B4 Tender items (`_rule_tenders_to_opportunity_items`)

- Loops **all** `rule_candidates` (300–700), not top 20.
- `session=None`; uses `hybrid_pairs` + `fresh_cache` dict — **no per-row DB** in current construction path.
- Each call: `resolve_hybrid_tender_score` in-memory.

**Expected complexity:** O(|rule_candidates|) — **0.5–3s** for 700 iterations.

### B5 Permits / awards (CPU on bundle rows)

- **200 permit rows:** `_score_construction_permit` each.
- **≤400 award rows:** `_score_contract_award` each.

**Expected complexity:** O(200 + 400) CPU — **0.5–2s** typical.

### B6 Assembly (`_assemble_construction_opportunities`)

- Sort + reserved slots (5 tender, 5 permit, awards backfill).

**Expected complexity:** O(candidates) — **&lt;0.1s**.

### B7 Breakdown (optimized path)

- `_attach_final_construction_tender_breakdowns(session, company, top, ...)` on **≤15 items**.
- Apply hybrid/cache breakdown from dicts; `_fill_missing_construction_breakdowns` batch-loads missing tenders once.

**Expected complexity:** O(|top|) ≤ 15 — **0.1–2s** after optimization (was higher if scoring all candidates pre-optimization).

### Construction typical wall-time budget (single request, no pool contention)

| Stage | P50 | P95 |
|-------|-----|-----|
| Read | 2s | 6s |
| Rule scan | 2s | 4s |
| Hybrid | 3s | 15s |
| Tender items | 1s | 3s |
| Permits + awards | 1s | 2s |
| Assembly | 0.05s | 0.1s |
| Breakdown (post-opt) | 0.2s | 2s |
| **Total CPU+DB** | **~9s** | **~32s** |

**>120s requires pool wait, retries, concurrency, or pathological data** (huge `fresh_cache`, cold hybrid 20× write, slow Postgres).

---

## 3. Architecture path (`_discover_architecture_opportunities`)

### Differences from construction

| Aspect | Architecture |
|--------|--------------|
| Tender rows | **400** `ArchTender` only |
| Awards | **None** (`award_rows=[]`) |
| Hybrid inline cap | **Applies** — max 5 fresh scores (not 20) |
| Extra tender surfacing | `_cached_ai_tenders_to_opportunity_items` loops **all** `fresh_cache` rows not in rule scan |
| Batch tender load | `_batch_load_tender_rows` for all cache keys in hybrid phase |
| Breakdown attach | **No** `_attach_final_construction_tender_breakdowns` |

### Architecture cached-AI loop

After rule-based tender items, iterates **every** row in `fresh_cache` (same 168h `tender_matches` set):

- Uses preloaded `cached_tender_rows` — **no N+1** if batch load succeeded.
- Can add **dozens–hundreds** of extra tender candidates to assembly pool if company has large match history.

**Expected complexity:** O(|fresh_cache|) — **1–10s** if |fresh_cache| &gt; 200.

### Architecture typical total

| Stage | P50 | P95 |
|-------|-----|-----|
| Read + batch cache tenders | 2s | 5s |
| Rule scan (~400) | 1s | 3s |
| Hybrid (cap 5 fresh) | 1s | 5s |
| Tender items + cached AI | 2s | 10s |
| Permits | 1s | 2s |
| Assembly | 0.05s | 0.1s |
| **Total** | **~7s** | **~25s** |

Architecture **usually faster** than construction unless `fresh_cache` is enormous.

---

## 4. Hybrid scoring path

```
_run_hybrid_tender_scoring
  → top 20 rule_candidates by rule_score
  → score_tender_pairs(session, company, kind, pair_candidates, persist=True, inline_cap=5)
```

### `score_tender_pairs` behavior

1. `load_fresh_company_tender_matches` — **full company history in window** (again).
2. For each of ≤20 candidates:
   - Hit: requires `breakdown_json` truthy (not just row exists).
   - Miss: `_load_tender_row` (PK get), deterministic score, upsert, commit at end.

### Construction vs architecture cap

```python
# pipeline/ai_matching.py ~470
if inline_cap is not None and fresh_scored >= inline_cap and kind != "construction":
    stats["skipped_cap"] += 1
    continue
```

**Construction bypasses inline cap** — design intent from spec 002 (deterministic construction scoring) but **amplifies DB write load**.

### Claude / AI in hybrid discover path

**None** in `score_tender_pairs` for discover—deterministic Python only. No Anthropic latency in this path unless a different code path is invoked.

### Redis / external cache

**Not used** in opportunity discovery (grep: no Redis in pipeline).

---

## 5. Database queries (inventory)

### Per construction discover (minimum query count)

| # | Query pattern | When |
|---|---------------|------|
| 1 | `Company` PK | Read |
| 2–3 | `Tender` LIMIT 400, `CommercialTender` LIMIT 400 | Read |
| 4–6 | Permit queries (own scan + market) | Read |
| 7–10 | Award queries (own, peers, clients, category) | Read |
| 11 | `tender_matches` by company (cutoff) | Read bundle |
| 12 | `tender_matches` again | Inside `score_tender_pairs` |
| 13 | Up to 20× `_load_tender_row` | Hybrid misses |
| 14 | 1× commit (upserts) | Hybrid |
| 15 | `tender_matches` again | Post-hybrid fresh_cache |
| 16 | Optional `tender_matches` | Breakdown if fresh_cache not passed (not case today) |
| 17 | Up to 15× batch tender IN query | `_fill_missing_construction_breakdowns` |

**Total:** ~15–45 round-trips per request depending on cache state.

### Pool configuration (`db/connection.py`)

```python
create_engine(url, pool_pre_ping=True, pool_recycle=1800)
# pool_size=5, max_overflow=10, pool_timeout=30 — SQLAlchemy defaults
```

**Max 15 concurrent connections** per process. API + pipeline subprocess + concurrent discovers **share one engine**.

---

## 6. Candidate counts (typical vs pathological)

| Set | Typical | Pathological |
|-----|---------|--------------|
| `tender_rows` loaded (construction) | 800 | 800 (fixed cap) |
| `rule_candidates` (open) | 300–500 | ~700 |
| Hybrid pair candidates | 20 | 20 |
| Hybrid fresh scores (construction) | 0–5 warm; **0–20 cold** | 20 + commit |
| `fresh_cache` rows (168h) | 20–80 | **200–500+** |
| `permit_rows` | 50–200 | 200 |
| `award_rows` | 50–400 | 400 |
| Final `top` matches | ≤15 | 15 |
| Architecture cached-AI extras | 0–50 | **100–300** |

### Permit load cost (`_load_permit_candidates`)

- Own permits: `SELECT … LIMIT 600` (`limit * 3`), filter `normalize_vendor_name(applicant)` in Python for **every row**.
- Market: `LIMIT 800` (`limit * 4`) with optional `OR` of up to 6 `contains` clauses on `permit_type`.

**Pathological:** Full table scan tendencies on large `permits` without selective indexes on `permit_type`.

### Award load cost (`_load_award_candidates`)

- `client_history`: `SELECT … LIMIT 800` on **all recent awards**, filter buyers in Python.
- Peer + category queries with array overlap.

**Pathological:** O(limit × 4) awards loaded for client matching regardless of index use.

---

## 7. N+1 risks

| Location | N+1? | Status |
|----------|------|--------|
| `_rule_tenders_to_opportunity_items` (construction) | Would call `get_fresh_cached_match` per row if `session` set | **Mitigated:** `session=None`, dict lookup |
| `score_tender_pairs` hybrid misses | 1 query per miss (up to 20) | **Bounded N+1** |
| `_cached_ai_tenders_to_opportunity_items` | Per-row `_load_tender_row` if no batch | **Mitigated** in arch via `_batch_load_tender_rows` |
| `_fill_missing_construction_breakdowns` | Per missing key | **Mitigated** via `_batch_load_tender_rows` |
| `resolve_hybrid_tender_score` with `session` | 1 query per call | **Avoided** in hot path |

**Residual N+1:** hybrid phase up to **20** `_load_tender_row` / `session.get` calls per discover (construction, cold cache).

**Hidden N×M:** `load_fresh_company_tender_matches` called **2–3 times** per construction discover—same wide SELECT repeated.

---

## 8. Redis / cache usage

| Layer | Used? | Notes |
|-------|-------|-------|
| Redis | **No** | |
| `tender_matches` PostgreSQL | **Yes** | 168h TTL; upsert on hybrid |
| In-memory `fresh_cache` dict | **Yes** | Per request |
| HTTP/CDN | Next.js `revalidate: 120` on proxy route | Caches **response**, does not speed first compute |

**Cache miss behavior:** Cold `tender_matches` or rows without `breakdown_json` → full hybrid scoring + breakdown fill.

**Cache growth risk:** More discovers → more upserts → larger `load_fresh_company_tender_matches` reads → **slowly increasing read phase**.

---

## 9. Infinite loops / retry storms

### Infinite loops

**None identified** in `opportunity_discovery.py` hot path. All loops bounded by `tender_rows`, `rule_candidates`, `permit_rows`, `award_rows`, `fresh_cache`, or `top`.

### Retry storms

| Mechanism | Risk | Detail |
|-----------|------|--------|
| `run_with_db_retry` on `get_session()` | Medium | 5 retries, 1–10s backoff — used by **other** API routes, not `session_scope` in discover |
| `session_scope` pool checkout | **High** | On `QueuePool` timeout (~30s wait), **no retry** — single wait per phase |
| **Concurrent discovers** | **Critical** | N requests × 3 phases competing for 15 connections → **cascading 30s waits** |
| User double-click Discover | Medium | Duplicate full discovers |
| Pipeline + discover overlap | Medium | Daily scrape holds connections during import |

**Retry storm math (pool exhaustion):**

```
3 session phases × 30s pool_timeout = 90s minimum wait per request
+ actual work (30–60s) = 120–150s+ per request
```

Multiple stuck requests **block pool longer** — matches production **>120s** and site-wide hangs (spec 004 root cause).

### Anthropic retry storms

Not applicable to discover hybrid path (deterministic). **BD intelligence** and `company_tender_match` endpoints still use Claude synchronously—separate from opportunities GET but **same pool** if same process.

---

## 10. Expected complexity by stage (reference)

| Stage | Time complexity | Dominant resource |
|-------|-----------------|-------------------|
| Read bundle | O(800 + P + A + F) rows | DB I/O, pool |
| Rule scan | O(800) | CPU |
| Hybrid scoring | O(20) scores + 3×F row load | DB writes |
| Tender items | O(R) rule candidates | CPU |
| Cached AI (arch) | O(F) fresh rows | CPU |
| Permits | O(P) ≤200 | CPU |
| Awards | O(A) ≤400 | CPU + bad queries |
| Assembly | O(C log C) | CPU |
| Breakdown | O(min(15, tenders in top)) | DB (batch) |

Where: R ≈ 300–700, P ≤ 200, A ≤ 400, F = fresh match rows, C = candidate lists.

---

## Why breakdown optimization did not fix &gt;120s

### What changed

`_attach_final_construction_tender_breakdowns` runs only on **final `top`** (≤15 tenders), with batch load + `_fill_missing_construction_breakdowns` for stragglers.

### What did not change

- Full read of 800 tenders + permits + awards.
- Full rule scan producing 300–700 candidates.
- Construction hybrid scoring up to **20** uncapped fresh DB scores.
- Triple `tender_matches` load.
- Permit/award heavy queries.
- **Connection pool contention** under concurrency.

### Order-of-magnitude estimate

If pre-optimization total = **180s** and breakdown was **30s** of that, post-optimization ≈ **150s** — **still &gt;120s**.

If breakdown was only **5–10s**, post-optimization ≈ **170s** — **no perceptible fix**.

**Instrumentation** (`breakdown_fill` in logs) should show breakdown &lt;2s while `total` &gt;120s — confirming diagnosis.

---

## Top 5 likely bottlenecks (ranked)

### 1. Database connection pool queuing (concurrency)

**Evidence:** Spec 004 `QueuePool limit of size 5 overflow 10`; 3 `session_scope()` phases per request; default `pool_timeout=30`.  
**Symptom:** Timeouts correlate with concurrent users; unrelated endpoints fail.  
**Why &gt;120s:** 90s+ wait + compute.

### 2. Construction hybrid scoring — uncapped fresh scores (up to 20)

**Evidence:** `inline_cap` skipped for `kind == "construction"`; each miss = row load + upsert; single commit after batch.  
**Symptom:** Slow first discover after cache expiry; `freshly_scored` near 20 in logs.  
**Why &gt;120s:** 20× DB latency + lock contention on `tender_matches` indexes.

### 3. Repeated `load_fresh_company_tender_matches` + growing row set

**Evidence:** Called in read bundle, inside `score_tender_pairs`, after hybrid; loads 168h+24h cutoff then Python-filters.  
**Symptom:** `read_ms` and `hybrid_write_ms` grow over weeks as match history grows.  
**Why &gt;120s:** Multi-second SELECTs + ORM hydration for 200–500 rows × 3.

### 4. Award `client_history` + permit own-scan queries

**Evidence:** `LIMIT 800` awards loaded; permit own path scans 600 applicants with Python normalization.  
**Symptom:** `read_ms` high for companies with many award clients / permit history.  
**Why &gt;120s:** Adds 5–30s on large tables without ideal indexes.

### 5. Architecture `_cached_ai_tenders_to_opportunity_items` on large `fresh_cache`

**Evidence:** Iterates all fresh matches not in rule scan; lowers effective assembly thresholds with hundreds of AI tenders.  
**Symptom:** `tender_items` phase slow on arch companies with heavy match history.  
**Why &gt;120s:** Large CPU + larger assembly input sets (secondary to construction for BC dashboard).

---

## Instrumentation plan

### Goals

1. Prove per-phase wall time vs pool wait.
2. Confirm breakdown is negligible post-optimization.
3. Capture candidate counts and cache stats per request.
4. Detect pool exhaustion vs slow queries.

### Implementation approach (no behavior change)

1. **Structured log line** at end of each discover (JSON one-liner) — parseable by Railway/log drain.
2. **Extend `SessionPhaseMetrics`** with `pool_wait_ms` per phase (time before first query after `session_scope` entry).
3. **Counter metrics** (Prometheus-style logs or simple aggregates) for daily review.

### Where to instrument

| File | Function | Add |
|------|----------|-----|
| `db/connection.py` | `session_scope` | Log checkout duration if &gt;1s |
| `pipeline/opportunity_discovery.py` | `_discover_construction_*` | Already has prints — add JSON + counts |
| `pipeline/opportunity_discovery.py` | `_discover_architecture_*` | Same |
| `pipeline/ai_matching.py` | `score_tender_pairs` | Log `fresh_cache_size`, `freshly_scored` |
| `api/main.py` | `company_opportunities` | Log total HTTP time + `kind` |

### Sampling

- **100%** in staging.
- **10%** sample in production until stable, then 100% for opportunities routes only (low QPS).

---

## Exact metrics to log

Per request (`company_id`, `kind`, `request_id`):

| Metric key | Type | Description |
|------------|------|-------------|
| `discover.total_ms` | float | End-to-end in discover function |
| `discover.read_ms` | float | B1 read phase |
| `discover.hybrid_write_ms` | float | B3 hybrid session |
| `discover.final_db_ms` | float | Breakdown session |
| `discover.cpu_ms` | float | Total − DB phases |
| `discover.breakdown_fill_ms` | float | Sub-span of final_db |
| `discover.breakdown_items` | int | Tenders with breakdown attached |
| `discover.tender_rows` | int | Loaded tender count |
| `discover.rule_candidates` | int | After rule scan |
| `discover.open_tenders` | int | Open in window |
| `discover.permit_rows` | int | |
| `discover.award_rows` | int | |
| `discover.fresh_cache_rows` | int | After final fresh load |
| `discover.hybrid.cache_hits` | int | From hybrid_scoring |
| `discover.hybrid.freshly_scored` | int | |
| `discover.hybrid.skipped_cap` | int | Should be 0 construction |
| `discover.tender_matches` | int | matches + stretch counts |
| `discover.final_matches` | int | len(top) |
| `discover.ranking_model` | string | |
| `discover.pool_wait_ms` | float | Sum across session phases |
| `http.total_ms` | float | API handler wall time |

Pool / process (periodic heartbeat every 60s):

| Metric key | Description |
|------------|-------------|
| `db.pool.checked_out` | SQLAlchemy pool status if exposed |
| `db.pool.overflow` | |
| `pipeline.running` | Subprocess lock |

---

## Fastest path to verify each hypothesis

### H1: Pool queuing (&gt;120s under concurrency)

| Step | Action | Pass criterion |
|------|--------|--------------|
| 1 | Run `scripts/verify_opportunities_concurrent.py` with 10 parallel company IDs | Any request &gt;60s; pool errors in logs |
| 2 | Add `pool_wait_ms` to one staging deploy | `pool_wait_ms` &gt; 30s on slow requests |
| 3 | Run 3 discovers while hitting `/api/permits` | Permits slow or timeout |

**Fastest:** Single production log grep for `QueuePool` or `timeout expired` during slow discover window.

### H2: Construction uncapped hybrid (20 fresh scores)

| Step | Action | Pass criterion |
|------|--------|--------------|
| 1 | Grep logs: `freshly_scored` for construction company | Value 15–20 on slow requests |
| 2 | Delete `tender_matches` for test company in staging; one discover | `hybrid_write_ms` &gt;10s |
| 3 | Re-run same company | `hybrid_write_ms` &lt;1s, `cache_hits` high |

**Fastest:** Staging cold-cache test one company; compare `hybrid_write_ms` before/after second request.

### H3: Triple `load_fresh` + large history

| Step | Action | Pass criterion |
|------|--------|--------------|
| 1 | `SELECT COUNT(*) FROM tender_matches WHERE company_id=X AND created_at > now()-interval '168 hours'` | Count &gt;100 |
| 2 | Correlate count with `read_ms` + `hybrid_write_ms` across companies | Positive correlation |
| 3 | Temporarily log row count inside each `load_fresh` call | 3 calls per request |

**Fastest:** SQL count for slowest production `company_id` from logs.

### H4: Award/permit read queries

| Step | Action | Pass criterion |
|------|--------|--------------|
| 1 | `EXPLAIN ANALYZE` permit own-query + award client-query on prod replica | Seq scan or &gt;500ms |
| 2 | Compare `read_ms` construction companies with heavy vs light award_clients | Heavy &gt;2× light |
| 3 | Temporarily skip award client block in staging branch | `read_ms` drops materially |

**Fastest:** `EXPLAIN ANALYZE` on award `LIMIT 800` query.

### H5: Breakdown not the culprit (post-optimization)

| Step | Action | Pass criterion |
|------|--------|--------------|
| 1 | Grep `breakdown_fill=` in production logs | `breakdown_fill` &lt;2s while `total` &gt;120s |
| 2 | Compare `final_db_ms` to `total` | `final_db_ms` &lt;5% of total |

**Fastest:** Log line already prints `breakdown_fill` — grep production.

---

## Recommended immediate actions (before async migration)

1. **Instrument** JSON metrics line + `pool_wait_ms` (1 PR).
2. **Apply construction `inline_cap`** to construction or cap at 5 in `score_tender_pairs` (product decision)—cuts worst-case hybrid DB work.
3. **Single `load_fresh` per discover** — pass dict into `score_tender_pairs`; remove duplicate SELECTs.
4. **Do not** rely on further breakdown optimizations for latency.
5. **Proceed with ADR v1.1 snapshot read path** — structural fix for &lt;3s SLA.

---

## Related documents

- [ADR-001 v1.1](./adr/001-opportunity-computation-architecture.md)
- [specs/004-scope-opportunities-db-sessions/spec.md](../../004-scope-opportunities-db-sessions/spec.md)
- [migration.md](./migration.md)

---

## Revision history

| Version | Date | Change |
|---------|------|--------|
| 1.0 | 2026-06-15 | Initial RCA |
