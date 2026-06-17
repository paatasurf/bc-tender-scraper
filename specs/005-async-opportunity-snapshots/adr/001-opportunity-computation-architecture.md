# ADR-001: Opportunity Computation Architecture

| Field | Value |
|-------|-------|
| **Status** | Accepted (v1.1) |
| **Date** | 2026-06-15 |
| **Feature** | `005-async-opportunity-snapshots` |
| **Deciders** | TenderScope engineering |
| **Supersedes** | Implicit default (synchronous HTTP discovery) |
| **Amends** | ADR-001 v1.0 (2026-06-15) |

## Context

TenderScope surfaces ranked construction and architecture opportunities (tenders,
permits, contract awards) per company profile. Today, `GET /api/companies/{id}/opportunities`
invokes `discover_opportunities()` synchronously inside the FastAPI request handler.

### Current behavior

- Loads the most recent ~400 tenders per source (not the full corpus), ~200 permits,
  ~200 awards per company.
- Rule-scans **all open tenders** in that window, hybrid-scores top candidates,
  scores permits/awards inline, assembles reserved-slot rankings, returns ~15 items.
- Production latency: **60–300+ seconds** per request under load; database connection
  pool exhaustion when concurrent discovers run (spec 004 addresses hold time, not
  compute volume).

### Scale assumptions (decision horizon: 24–36 months)

| Parameter | Value |
|-----------|-------|
| Companies | 20,000+ (growing ~100/day) |
| Tenders (total corpus) | Millions |
| Ingest | Continuous (~1,000 new tenders/day) |
| Matching | Deterministic Python scoring + optional AI explanations (constitution-compliant) |
| Read SLA | **P95 &lt;3 seconds** opportunity retrieval |
| Freshness SLA (active cohort) | **P95 &lt;30 minutes** after ingest completes |

### Constraints (constitution v1.0.0)

1. Scores must be transparent and decomposable in API/UI.
2. Claude generates human-readable text only—never scores or rankings.
3. Location matching at city/region level only.
4. Consistent JSON API envelopes.
5. All scoring logic in Python.

### Decision drivers

- Separate **read latency** from **compute throughput**.
- Preserve ranking parity with existing `discover_opportunities()` output.
- Trigger full discover only for bounded company cohorts—not naive N×M fan-out.
- Operate on Railway (FastAPI + PostgreSQL + Redis) without unsustainable ops burden.

---

## Principal Engineer review (v1.0 → v1.1)

v1.0 **overstated incremental efficiency** and **underspecified consistency and
recovery**. v1.1 corrections:

| v1.0 claim | v1.1 correction |
|------------|-----------------|
| Incremental pair + assembly workers | **Primary job = Tier B full `discover_opportunities()`**; pair pre-warm is Tier C optimization only |
| Scalability 5/5 | **3/5** for compute (bounded but not elastic); **5/5** for read path |
| Failure recovery 4/5 | **3/5** until outbox + versioned snapshots + DB-backed jobs ship |
| `entity_versions` polling | **Outbox table from day 1** (same transaction as ingest) |
| DELETE + INSERT snapshots | **Versioned swap** (no empty read window) |
| `FORCE_LIVE_DISCOVERY` rollback | **Rejected** for incident rollback; snapshots + per-company admin recompute |
| BD intelligence P3 | **P1** if feature flag enabled in production (same pool risk) |
| Golden tests on ~20 companies | **Insufficient**; add fan-out cap, ordering, and enrichment gate tests |

---

## Options Considered

1. **Current synchronous HTTP computation** — status quo.
2. **n8n workflow orchestration** — visual workflows for ingest/notifications.
3. **Python worker + queue + snapshot architecture** — **selected**.
4. **Event-driven architecture** — broker-backed events; enqueue evolution inside Option 3.

### Summary matrix (v1.1 scores)

| Dimension | 1. Sync HTTP | 2. n8n | 3. Worker + snapshot | 4. Event-driven |
|-----------|:------------:|:------:|:--------------------:|:---------------:|
| **Scalability** | 1 | 2 | **3** (read: 5) | 5 |
| **Operational complexity** | 4 | 3 | **4** | 2 |
| **Failure recovery** | 2 | 2 | **3** | 4 |
| **AI scoring compatibility** | 4 | 1 | **5** | 4 |
| **Infrastructure cost** | 4 | 3 | **3** | 2 |
| **Developer productivity** | 3 | 2 | **4** | 3 |
| **Long-term maintainability** | 2 | 2 | **4** | 4 |

**Rejected:** Option 1 (HTTP compute), Option 2 (n8n for scoring).  
**Selected:** Option 3. Option 4 enqueue layer (outbox → Redis Streams) adopted **inside** Option 3, not as replacement.

---

## Decision

**Adopt Option 3: Python worker + queue + snapshot read model.**

| Layer | Responsibility |
|-------|----------------|
| **HTTP** | Read snapshots only; enqueue refresh; never full discover (default) |
| **Workers** | Tier B full discover → versioned snapshot write |
| **Outbox** | Durable enqueue from ingest (same DB transaction) |
| **Redis** | Delivery acceleration + debounce keys (not sole source of truth) |
| **PostgreSQL** | Snapshots, pairs, jobs, outbox |

**Rejected for core matching:** n8n, sync HTTP discover, global live-discover rollback.

**Evolution:** Redis Streams or managed queue as outbox consumer transport after metrics justify it.

---

## Compute model (honest tiers)

Current code **cannot** assemble parity-safe snapshots from pair tables alone.
`_rule_tenders_to_opportunity_items` requires full `rule_candidates` from scanning
~800 tenders; permits/awards are scored inline in discover, not from materialized
pair tables today.

### Tier A — Snapshot read (HTTP)

- `SELECT` from `company_opportunity_snapshot_meta` + `company_opportunity_snapshots`
  where `snapshot_version = meta.active_version`.
- **No** `discover_opportunities()`.
- Target: &lt;500ms DB + &lt;3s P95 end-to-end.

### Tier B — Full discover (workers) **primary correctness path**

- Call `discover_opportunities()` (post–spec 004 phased sessions).
- Write versioned snapshot via swap protocol (see Snapshot versioning).
- **This is the only parity-safe compute job** until Phase 2 refactor extracts
  `generate_candidates()` / `assemble()` from the monolith.

### Tier C — Pair pre-warm (optional optimization)

- Upsert `tender_matches` on ingest fan-out.
- **Does not replace Tier B**; reduces hybrid scoring time inside Tier B.
- Skippable under load; correctness unaffected.

### Job types (v1.1)

| Job | Tier | Trigger |
|-----|------|---------|
| `full_discover_company` | B | New company, user refresh, nightly active cohort, enrichment complete |
| `fan_out_coordinator` | — | Expands outbox events into debounced Tier B jobs (not Tier C-only) |
| `pair_prewarm_batch` | C | Optional after tender delta; low priority |
| `explain_match` | — | Async Claude text only; never blocks Tier B |
| `nightly_active_refresh` | B | Cron; active cohort only |
| `reconcile_stale` | B | Meta `active_version` behind `signals_version` |

**Forbidden:** Separate “assembly worker” that skips rule scan and claims parity.

---

## System architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Vercel — React (freshness UI, poll status)                             │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────────┐
│  Railway — tenderscope-api (FastAPI)                                    │
│  • GET opportunities → snapshot read (Tier A)                             │
│  • GET status / POST refresh → enqueue via outbox                         │
│  • APScheduler → pipeline subprocess only (no discover in API)          │
│  pool_size ≤ 5 (reads only)                                             │
└───────────────┬─────────────────────────────┬───────────────────────────┘
                │                             │
┌───────────────▼──────────────┐   ┌────────────▼────────────────────────────┐
│  Railway — Redis             │   │  Railway — PostgreSQL                   │
│  • Debounce keys             │   │  • Core entities                        │
│  • ARQ queue (accelerator)   │   │  • outbox_events                        │
│  • AOF persistence required  │   │  • match_jobs (source of truth)         │
└───────────────┬──────────────┘   │  • tender_matches (+ permit/award)      │
                │                  │  • company_opportunity_snapshots (ver)  │
┌───────────────▼──────────────┐   └────────────┬────────────────────────────┘
│  Railway — tenderscope-worker│                │
│  • Outbox poller             │◄───────────────┘
│  • Tier B discover workers   │   pool_size ≤ 8 per worker replica
│  • Tier C pre-warm (optional)│
│  • Reaper + reconcile cron   │
└───────────────┬──────────────┘
                │
┌───────────────▼──────────────┐
│  pipeline subprocess         │
│  scrape → import → outbox    │
│  (same TX as entity upsert)  │
└──────────────────────────────┘
```

### Ingest ordering contract

1. Import entity rows.
2. Write `outbox_events` in **same transaction**.
3. Company intelligence / enrichment subprocess completes.
4. Write `outbox_events` (`company.enriched`) with `signals_version` bump.
5. **Tier B jobs MUST check** `company.signals_version ≤ job.expected_signals_version`
   or re-queue if enrichment not yet applied.

---

## Snapshot versioning

### Problem (v1.0)

DELETE + INSERT in one transaction still risks long locks; failed mid-write leaves
no snapshot; readers cannot distinguish versions.

### Versioned swap protocol

**Tables:**

- `company_opportunity_snapshot_meta`: `active_version` (int), `computed_at`,
  `freshness`, `ranking_model`, `input_version_hash`, `signals_version`,
  `job_id`.
- `company_opportunity_snapshots`: includes `snapshot_version` (int); multiple
  versions may exist briefly.

**Write sequence (single transaction):**

1. `new_version = meta.active_version + 1`
2. `INSERT` up to 15 snapshot rows with `snapshot_version = new_version`
3. `UPDATE meta SET active_version = new_version, computed_at = now(), ...`
4. `COMMIT`
5. Async cleanup job: `DELETE` rows where `snapshot_version < active_version - 1`

**Read path:** Always `WHERE snapshot_version = meta.active_version`.

**Rollback of bad ranking deploy:** Set `active_version` to previous known-good
version (rows retained until cleanup); or re-run Tier B with fixed code and new
version.

### `input_version_hash` definition

SHA-256 over canonical JSON of:

- `ranking_model` string
- `company_kind`, `company_id`, `signals_version`
- Recent-window policy constants (`max_candidates=400`, source limits)
- `min_score` default for snapshot (stored per meta)

Enables parity debugging without re-running discover.

---

## Consistency guarantees

| Guarantee | Level | Mechanism |
|-----------|-------|-----------|
| Snapshot read atomicity | **Strong** | Single `active_version` pointer; readers never see partial new version |
| Outbox → job delivery | **At-least-once** | Outbox poller marks `published_at`; workers idempotent on `(company_kind, company_id, job_type, dedupe_key)` |
| Pair table vs snapshot | **Eventual** | Pairs may be newer than snapshot until Tier B completes; HTTP reads snapshot only |
| Enrichment before discover | **Best-effort ordered** | `signals_version` gate on Tier B jobs; stale job re-queued |
| Fan-out cap | **Intentional loss** | Capped companies may miss tender until next Tier B; metric `fan_out_capped` |
| Cross-service | **No linearizable** | API may show stale snapshot while worker runs; `freshness` field exposes this |
| AI explanations | **Eventual** | `reasoning` may update after snapshot; scores never change post-snapshot |

**Not guaranteed:** Every company sees every new tender within freshness SLA if
outside active cohort or fan-out cap. **Product acceptance required.**

**Inactive companies (~18k of 20k):** Best-effort freshness on view-triggered
Tier B; no SLA.

---

## Compute budget calculations

### Per-job cost (measured targets; validate in staging)

| Job type | CPU time | DB reads (approx) | DB writes |
|----------|----------|-------------------|-----------|
| Tier B `full_discover_company` | **8s** (P50), **15s** (P95) | ~1,200 rows | 15 snapshot rows + pair upserts |
| Tier C `pair_prewarm_batch` (100 pairs) | **0.2s** | ~200 | ~100 upserts |
| Outbox fan-out coordinator | **1–60s** | batch | job inserts |

Constants used for budgeting: **10s mean** per Tier B job (conservative).

### Daily job budget (20k companies, 1k tenders/day)

| Source | Tier B jobs/day | Calculation |
|--------|-----------------|-------------|
| New companies | **100** | 100/day × 1 full discover |
| Nightly active cohort | **2,000** | 10% of 20k companies |
| Ingest fan-out (debounced) | **3,000–6,000** | ~500 companies/tender × 1k tenders, heavy debounce (5–15 min), coalesce to one job per company per window |
| User refresh | **500** | Assumption: 500 requests/day, deduped |
| Reconcile (signals drift) | **200** | ~1% active cohort |
| **Total Tier B** | **~5,800–8,800** | Ceiling with margin: **10,000/day** |

**Rejected budget:** 20,000 nightly full discovers (all companies) = 200,000s ≈ **55h** single core.

**Rejected budget:** Naive 1k × 20k pair-only without Tier B = **incorrect parity**.

### Daily CPU seconds

```
Tier B: 10,000 jobs × 10s = 100,000 CPU-s/day
Tier C (optional, 20% of pair batches): 50,000 pairs × 0.002s = 100 CPU-s/day
Overhead (outbox, reaper): 5,000 CPU-s/day
────────────────────────────────────────────
Total ≈ 105,000 CPU-s/day ≈ 29.2 CPU-hours/day
```

### Pair write budget (Tier C optional)

Target: **≤500,000** `tender_matches` upserts/day (prefiltered fan-out).  
Steady-state pair rows (168h TTL): ~600k rows (~1.8 GB).

### Storage budget

| Store | Rows | Est. heap |
|-------|------|-----------|
| Snapshots (15 × 20k, 2 versions retained) | ~600k | ~2 GB |
| `tender_matches` | ~600k | ~1.8 GB |
| `match_jobs` (30d retention) | ~300k | ~500 MB |
| `outbox_events` (7d retention) | ~100k | ~100 MB |

**Gate:** Alert if PostgreSQL opportunities tables exceed **8 GB**.

---

## Worker sizing calculations

### Required sustained throughput

```
Required core-hours/day = 105,000 CPU-s ÷ 3600 ≈ 29.2 core-hours
Available window = 20 hours (avoid peak API/pipeline overlap 06:00–08:00)
Required cores (theoretical) = 29.2 ÷ 20 ≈ 1.5 cores sustained
```

### Peak factor (post-daily scrape)

Assume **40%** of daily Tier B jobs in **2 hours** after scrape:

```
Peak jobs = 4,000 in 2h = 2,000/h ≈ 33 jobs/min
At 10s/job, need ≈ 33 × 10s = 330s CPU per minute ≈ 5.5 cores peak
```

### Recommended Railway sizing (v1.1)

| Service | Spec | Rationale |
|---------|------|-----------|
| `tenderscope-worker` | **2 replicas × 2 vCPU** (4 vCPU total) | Peak ~5.5 cores with headroom; 2 replicas for deploy continuity |
| `tenderscope-api` | 1–2 vCPU | Read-only; no discover |
| Redis | **512 MB–1 GB**, AOF `everysec` | Queue + debounce; not sole durability |
| PostgreSQL | Existing plan + monitor connections | See connection budget |

### Connection budget (PostgreSQL)

| Service | `pool_size` | `max_overflow` | Max connections |
|---------|-------------|----------------|-----------------|
| API | 5 | 5 | 10 |
| Worker (per replica) | 4 | 4 | 8 × 2 = **16** |
| Pipeline subprocess | 3 | 2 | 5 |
| **Total peak** | | | **~31** |

Reserve headroom below provider limit (typically 97–100 on Railway Postgres small).
**Alert** at 70% of max connections.

### Autoscaling triggers (manual v1; automate when metrics stable)

| Metric | Scale up when |
|--------|----------------|
| `match_jobs` pending &gt; 500 for 10 min | Add worker replica |
| Outbox unpublished &gt; 5,000 | Increase poller frequency |
| Tier B P95 duration &gt; 20s | Investigate DB; add worker CPU |
| DLQ depth &gt; 50 | Page on-call; do not auto-scale |

---

## Failure recovery strategy

### Principles

1. **Users always get last good snapshot** (or explicit `freshness: missing`).
2. **Jobs are idempotent**; safe to retry.
3. **PostgreSQL outbox + `match_jobs`** are durability source of truth—not Redis.
4. **No global revert to sync HTTP** under incident response.

### Failure modes and responses

| Failure | User impact | Recovery |
|---------|-------------|----------|
| Tier B job fails mid-discover | Stale snapshot served | Auto-retry (3×, exponential backoff); DLQ after max |
| Tier B succeeds, snapshot swap fails | Stale snapshot | Retry job; old `active_version` unchanged |
| Outbox poller stuck | Delayed freshness | Reaper restarts poller; alert on unpublished &gt; 15 min |
| Worker OOM | Delayed freshness | Kubernetes/Railway restart; job returns to `pending` via visibility timeout |
| Bad deploy (wrong scores) | Wrong rankings | Rollback `active_version` or deploy fix + mass Tier B with new `ranking_model` |
| Fan-out cap exceeded | Missed matches for some companies | Overflow queue (low priority); metric alert; manual backfill script |
| Partial import | Incomplete data | Import marks `pipeline_runs` failed; outbox not published for partial batch |

### Reconciliation cron (every 15 min)

1. `match_jobs` where `status=running` and `started_at &lt; now() - 30 min` → `failed`, retry.
2. `company_opportunity_snapshot_meta` where `signals_version` &gt; hash input version → enqueue Tier B.
3. `freshness=stale` and `computed_at &lt; now() - 2× SLA` for active cohort → enqueue Tier B (priority bump).
4. Orphan snapshot versions older than `active_version - 1` → cleanup DELETE.

### Break-glass (allowed)

- `POST /admin/companies/{id}/opportunities/recompute` — single-company Tier B (auth required).
- CI / scripts: `discover_opportunities()` for parity—not production HTTP path.
- **`FORCE_LIVE_DISCOVERY` on API: disabled in production** (config guard).

---

## Redis outage strategy

### Failure modes

| Mode | Symptom | Detection |
|------|---------|-----------|
| Redis down | Enqueue via ARQ fails | Health check; API logs `redis_unavailable` |
| Redis memory full | Eviction / OOM | Redis `used_memory` alert |
| Redis data loss (no AOF) | Lost debounce keys; duplicate jobs | Acceptable; idempotent jobs |

### Runtime behavior

**API (Redis down):**

- GET opportunities: **continue** (PostgreSQL snapshot read).
- POST refresh: write `match_jobs` + `outbox_events` directly (degraded enqueue path).
- Return `freshness` as today; log enqueue degradation.

**Workers (Redis down):**

- Outbox poller reads `outbox_events` + `match_jobs` from PostgreSQL every **30s**.
- Tier B workers poll `match_jobs WHERE status=pending ORDER BY priority`.
- Debounce coalescing disabled → **more Tier B jobs** (compute spike); alert ops.

**Recovery:**

1. Restore Redis.
2. Run reconciliation cron (drains pending `match_jobs`).
3. Verify queue depth returns to baseline within 2h.

**Configuration requirements:**

- `appendonly yes`, `appendfsync everysec`.
- `maxmemory-policy noeviction` (or monitor; job loss unacceptable).

---

## Postgres outage strategy

### Failure modes

| Mode | Behavior |
|------|----------|
| Transient (recovery mode, connection refused) | API + workers retry with existing `TRANSIENT_DB_ERROR_MARKERS` |
| Extended outage | API 503 on opportunities (no live discover fallback) |
| Failover / restart | In-flight transactions roll back; jobs retry |

### API behavior

- **Cannot read snapshots:** return **503** with consistent error envelope—not sync discover.
- Health endpoint reports `db: degraded`.

### Worker behavior

- Pause outbox poller and Tier B workers after retry exhaustion.
- Jobs remain `pending` in `match_jobs`.
- On recovery: workers resume; **no job loss** if commits never marked complete.

### Pipeline subprocess

- File lock prevents duplicate pipeline; import transaction rolls back on failure.
- **Outbox not published** if import TX fails—no phantom jobs.

### Post-recovery

1. Verify `match_jobs` pending count vs outbox unpublished count.
2. Run reconciliation cron.
3. Monitor connection count spike (all services reconnect).

### Long-term (optional)

- Read replica for Tier A snapshot SELECTs when primary write load &gt; 60% CPU.

---

## DLQ and replay strategy

### Dead-letter criteria

Job moves to DLQ when:

- `attempts >= MAX_RETRIES` (Tier B: 3, coordinator: 5).
- Poison payload (invalid `company_id`, schema validation fail).
- Non-transient errors (e.g. `ValueError` company not found after 1 attempt → dead immediately).

### Storage

| Field | Location |
|-------|----------|
| Job payload, error, stack | `match_jobs` where `status = dead` |
| Mirror list | Redis `opportunities:dlq` (optional index) |

Retention: **90 days** in PostgreSQL; export to S3/archive before purge if audit needed.

### Replay procedure (runbook)

1. **Triage:** Identify `job_type`, `error`, count (`SELECT COUNT(*) FROM match_jobs WHERE status='dead' AND created_at > ...`).
2. **Root cause:** Fix code/config/deploy—not replay until fixed.
3. **Single replay:** `scripts/replay_job.py --job-id &lt;uuid&gt;`  
   - Resets `status=pending`, `attempts=0`, increments `replay_generation`.
4. **Bulk replay:** `scripts/replay_job.py --job-type full_discover_company --since &lt;ts&gt; --limit 100`  
   - **Rate limit:** max 50 replays/min to avoid compute storm.
5. **Verify:** Check `active_version` incremented; parity spot-check 5 companies.
6. **Post-mortem:** Document in incident log; update alert threshold if needed.

### Replay safety

- Idempotent: Tier B writes new `snapshot_version`; replays do not duplicate active rows.
- **Never** bulk replay all 20k companies without compute budget approval.

### DLQ alerts

| Condition | Severity |
|-----------|----------|
| DLQ depth &gt; 10 in 1h | Warning |
| DLQ depth &gt; 50 | Page |
| Same `error` &gt; 20 jobs | Page (deploy regression) |

---

## Deployment rollback plan

### Pre-deploy gates

- [ ] Golden parity CI pass (≥10 construction + ≥10 architecture companies).
- [ ] Spec 004 phased sessions merged.
- [ ] Snapshot coverage ≥ **95%** of active cohort backfilled.
- [ ] `OPPORTUNITIES_READ_MODE=snapshot` tested in staging ≥ 48h.

### Deploy sequence (forward)

1. Migrate schema (outbox, versioned snapshots, `match_jobs`).
2. Deploy workers + Redis (snapshot mode off).
3. Backfill Tier B for active cohort.
4. Deploy API with `OPPORTUNITIES_READ_MODE=snapshot` (staging → prod canary 10% → 100%).
5. Deploy frontend `freshness` UI.

### Rollback sequence (incident)

| Step | Action | **Do not** |
|------|--------|------------|
| 1 | Set `OPPORTUNITIES_READ_MODE=snapshot` stays **on** if only ranking bug | Enable global live discover |
| 2 | Ranking bug: rollback `active_version` via admin script OR redeploy previous API/worker image | Delete snapshot tables |
| 3 | Worker bug: scale workers to 0; snapshots still served (stale) | Force live HTTP discover |
| 4 | Schema bug: restore DB backup only if migration irreversible (last resort) | — |
| 5 | Per-company: admin recompute for affected IDs | — |

### Canary criteria (10% traffic or feature cohort)

- P95 read &lt; 3s.
- DLQ depth = 0 for 24h.
- `fan_out_capped` &lt; 5% of tender deltas.
- No pool exhaustion on API.

### Version pinning

- `ranking_model` in meta must match deployed worker code version string.
- Deploy checklist: bump `ranking_model` only when scoring logic changes; triggers optional mass Tier B.

---

## Operational runbooks

### Runbook 1: Opportunity reads slow (&gt;3s P95)

1. Check API logs for accidental live discover (`OPPORTUNITIES_READ_MODE`).
2. Check PostgreSQL slow queries on snapshot tables; verify index `(company_kind, company_id, snapshot_version)`.
3. Check connection pool saturation on API.
4. Consider read replica if write contention from workers.
5. **Do not** enable live discover.

### Runbook 2: Stale opportunities (&gt;30 min after ingest)

1. Check `pipeline_runs` / scrape completion time.
2. Check `outbox_events` unpublished count and oldest `created_at`.
3. Check `match_jobs` pending depth and worker replica health.
4. Check enrichment subprocess finished before discover (`signals_version` gate failures in logs).
5. Run reconciliation cron manually; scale worker replicas if pending &gt; 500.

### Runbook 3: DLQ growing

1. Query top errors: `GROUP BY error LIMIT 10`.
2. If deploy correlated → rollback worker image (Runbook 5).
3. If data issue → fix source row; replay affected jobs (rate-limited).
4. If fan-out cap → review `fan_out_capped` metric; run overflow queue drain overnight.

### Runbook 4: Redis unavailable

1. Confirm API still serves snapshots (Tier A).
2. Verify workers polling PostgreSQL `match_jobs`.
3. Expect compute spike (no debounce); scale workers temporarily.
4. Restore Redis; run reconciliation.
5. Verify AOF persistence enabled post-incident.

### Runbook 5: Postgres unavailable / degraded

1. API returns 503 for opportunities—communicate status page.
2. Pause workers (avoid connection storm).
3. Wait for provider recovery; verify health.
4. Resume workers; run reconciliation.
5. Review pending job age; extend freshness SLA communication if backlog &gt; 2h.

### Runbook 6: Wrong rankings after deploy

1. Identify `ranking_model` version change in deploy.
2. Rollback `active_version` for affected companies (if previous version rows exist).
3. Or redeploy previous worker release.
4. Queue Tier B for active cohort with rate limit.
5. Post-mortem: golden test gap analysis.

### Runbook 7: Daily scrape overlap with worker peak

1. Expected: spike 06:00–08:00 Vancouver after scheduler.
2. If lag &gt; 1h: shift worker-heavy cron away from scrape window OR add temporary replica.
3. Pipeline file lock prevents duplicate scrape—do not force second pipeline.

### On-call metrics dashboard (minimum)

| Metric | Source |
|--------|--------|
| Opportunity GET P95 latency | API |
| `match_jobs` pending / running / dead counts | PostgreSQL |
| Outbox oldest unpublished age | PostgreSQL |
| Tier B job duration P50/P95 | Worker logs |
| `fan_out_capped` rate | Worker metrics |
| Active snapshot age P95 (active cohort) | PostgreSQL |
| PostgreSQL connections used | Provider |
| Redis memory / connected clients | Redis |
| DLQ depth | PostgreSQL |

---

## Implementation guardrails (v1.1)

1. **Tier B only** for parity: `discover_opportunities()` → versioned snapshot write.
2. HTTP MUST NOT run full discover when `OPPORTUNITIES_READ_MODE=snapshot`.
3. **Outbox** written in same transaction as ingest upserts.
4. **Enrichment gate:** Tier B checks `signals_version`.
5. **Fan-out cap** with overflow queue + metric; never silent cap without alert.
6. Claude: **Tier `explain_match` only**; `ENABLE_AI_EXPLANATIONS_IN_WORKERS` default `false`.
7. Snapshots: versioned swap; no DELETE-before-INSERT visible to readers.
8. `match_jobs` + outbox = durability; Redis = accelerator.
9. BD intelligence: snapshot read path **P1** if `BD_INTELLIGENCE_ENABLED` in prod.
10. Phase 2 refactor (optional): extract `generate_candidates` / `assemble` for true incremental assembly.

---

## Rationale (v1.1)

1. **Sub-3s reads** require snapshot Tier A; proven at scale only with versioned storage.
2. **Compute honesty:** Tier B full discover is expensive (~10k/day); budgeted worker fleet required.
3. **Incremental pair-only was incorrect** for current code; Tier C is optimization only.
4. **Outbox-first** closes enqueue gap between import commit and worker pickup.
5. **n8n rejected** for scoring; notifications remain optional peripheral use.
6. **Rollback serves snapshots**, not sync HTTP—prevents repeating pool exhaustion incidents.

---

## Consequences

### Positive

- Read/compute separation with explicit consistency model.
- Operable failure modes (Redis down, Postgres degraded, DLQ replay).
- Compute and worker sizing grounded in formulas.
- Ranking deploy rollback via snapshot version without live discover.

### Negative

- Higher operational surface (runbooks, dashboards, reconciliation cron).
- Inactive companies and cap-hit companies: intentional freshness gaps.
- ~29 CPU-hours/day baseline compute cost on workers.

### Neutral

- Event broker (Redis Streams) may replace outbox poller transport later.
- Spec 004 phased sessions apply to Tier B workers.

---

## Related documents

- [spec.md](../spec.md) — Feature specification (to be aligned with v1.1 ADR)
- [specs/004-scope-opportunities-db-sessions/spec.md](../../004-scope-opportunities-db-sessions/spec.md)
- `.specify/memory/constitution.md`

---

## Revision history

| Version | Date | Change |
|---------|------|--------|
| 1.0 | 2026-06-15 | Initial draft (superseded) |
| 1.1 | 2026-06-15 | Principal Engineer review: honest compute tiers, outbox, versioned snapshots, recovery/runbooks, revised scores, rollback policy |
