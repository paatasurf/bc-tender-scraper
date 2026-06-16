# Research: Scoped Database Sessions for Opportunities Discovery

**Feature**: `004-scope-opportunities-db-sessions` | **Date**: 2026-06-16

## R1 — Root Cause of Pool Exhaustion

**Decision**: Single session per opportunities request holds a pool connection for the full discover duration.

**Rationale**:

- `api/main.py` lines 463–482 / 583–602: `get_session()` before `discover_opportunities()`, `session.close()` only in `finally` after full return.
- `discover_opportunities()` passes session through `_discover_*_opportunities` for 60–300+ seconds.
- Default pool: `pool_size=5`, `max_overflow=10`, `pool_timeout=30` (`db/connection.py` lines 271–279 — only `pool_pre_ping` and `pool_recycle=1800` set explicitly).
- ~16 concurrent or stuck discover requests exhaust 15 connections; new requests (including `/api/permits`) block 30s then raise `TimeoutError`.

**Alternatives considered**:
- Raise pool limits — masks symptom, does not fix multi-minute holds (rejected as primary fix per spec FR-008).
- Request queuing middleware — adds latency, does not reduce hold time (deferred; may complement later).

---

## R2 — Phased Session Pattern

**Decision**: Split discover into **Read → CPU → Write → CPU → Final DB** phases; each DB phase uses `with session_scope():` and closes before next phase.

**Rationale**:

- SQLAlchemy sessions bind one connection from the pool until `close()`.
- Current pipeline already uses plain dicts for many structures (`RuleTenderCandidate.payload`, `_tender_payload`, `CompanySignals`); ORM rows needed only at load and persist boundaries.
- Hybrid `score_tender_pairs` is the only write-heavy step (upsert + commit); isolating it limits write connection time to seconds.
- Final breakdown attach (construction) runs on ≤15 items — ideal final short session.

**Alternatives considered**:
- `session.expunge_all()` mid-request without close — connection still checked out (rejected).
- `persist=False` for all hybrid during discover — faster but changes cache side effects for subsequent requests (rejected; spec assumes persist continues).

---

## R3 — Data Detachment Strategy

**Decision**: At end of read phase, convert ORM entities to existing plain structures; do not pass `Session`-bound ORM objects into CPU functions.

**Rationale**:

- `_tender_payload(row, source)` already produces JSON-serializable dicts used in `RuleTenderCandidate`.
- `CompanySignals.from_company(company)` extracts scalars/lists from company row — can be built from dict snapshot if company row loaded in read phase.
- `score_construction_match(company, tender, source)` needs company + tender attributes — use expunged ORM objects (`session.expunge()`) or lightweight namespace built from read-phase dicts (prefer expunge for minimal diff to scoring code).

**Alternatives considered**:
- Duplicate scoring to accept dicts only — large diff, risks parity drift (rejected).
- Raw SQL everywhere — unnecessary rewrite (rejected).

---

## R4 — Route Handler Session Ownership

**Decision**: Move session lifecycle entirely into `discover_opportunities()`; route handlers become session-free.

**Rationale**:

- Prevents accidental reintroduction of long-lived session at API layer.
- Matches spec FR-002 (discrete phases owned by pipeline).
- Other endpoints keep existing `get_session()` / `finally: close()` pattern unchanged.

**Alternatives considered**:
- FastAPI `Depends(get_db)` generator — still one session per request unless discover internal phases manage separately (rejected for opportunities; use internal phases only).

---

## R5 — Parity Verification Strategy

**Decision**: Golden JSON snapshots for pinned company IDs + query params; pytest compares full response after refactor.

**Rationale**:

- Spec SC-003 requires identical match IDs, scores, order.
- Baseline captured from last known-good deploy or local DB seed before session refactor.
- Construction: company 1921, `min_score=50`, `limit=15`. Architecture: company 19, `min_score=40`, `limit=15`.

**Alternatives considered**:
- Manual QA only — insufficient for ranking regression (rejected).
- Hash of scores only — misses ordering and breakdown (rejected).

---

## R6 — Concurrent Load Validation

**Decision**: Script in quickstart fires 5 parallel discover requests + simultaneous `/api/health` and `/api/permits?limit=10`.

**Rationale**:

- Directly validates SC-001 and SC-002.
- Reproduces production failure mode without needing 16 clients.

**Alternatives considered**:
- Locust/k6 — overkill for initial validation (optional later).

---

## R7 — get_session Retry Holding Connection

**Decision**: Out of scope for this feature; optional follow-up in `db/connection.py` to close session before retry sleep.

**Rationale**:

- Spec scopes opportunities path only.
- Retry hold worsens pool pressure but is not the primary 60–300s leak.
- Document as related finding for future fix.

---

## R8 — Background init_db Pool Contention

**Decision**: No change to init_db thread in this feature; phased discover reduces concurrent hold time so init + discover less likely to starve pool together.

**Rationale**:

- Feature 003 already moved init to background.
- Separate init engine pool is optional future work.

**Alternatives considered**:
- Dedicated migration pool — valid but out of scope.
