# Data Model: Scoped Database Sessions for Opportunities Discovery

**Feature**: `004-scope-opportunities-db-sessions` | **Date**: 2026-06-16

This feature does not introduce new database tables. It defines **in-memory phase boundaries** and session lifecycle entities passed between discover phases without an active connection.

## Runtime Entities

### DiscoveryRequest

Input to the opportunities pipeline (unchanged externally).

| Field | Type | Source |
|-------|------|--------|
| company_id | int | Path param |
| kind | `"construction"` \| `"architecture"` | Query (construction route) or fixed (arch route) |
| min_score | int | Query, default per kind |
| limit | int | Query, default 15 |
| max_candidates | int | Internal default 400 |

### DiscoveryReadBundle

Produced by **Read phase**; consumed by CPU phases. No session required after construction.

| Field | Type | Description |
|-------|------|-------------|
| company | Company \| ArchCompany (expunged) | Firm profile for scoring |
| signals | CompanySignals | Derived scalars/lists for rule scoring |
| tender_rows | list[tuple[payload dict, source str]] | Up to 800 construction or 400 arch tenders |
| permit_rows | list[tuple[Permit (expunged), own bool]] | Pre-filtered permit candidates |
| award_rows | list[tuple[ContractAward (expunged), context str]] | Construction only |
| fresh_cache | dict[(source, tender_id), TenderMatch row or snapshot] | Weekly cache for hybrid resolution |

**Validation**: All ORM objects MUST be expunged or converted before session close; no lazy loads after read phase.

### HybridWriteResult

Produced by **Hybrid Write phase**.

| Field | Type | Description |
|-------|------|-------------|
| pairs | dict[(source, id), pair_data] | Scores, breakdown, reasoning from hybrid |
| stats | dict | cache_hits, freshly_scored, etc. (unchanged) |

### DiscoveryCpuState

Intermediate state during CPU phases (no DB).

| Field | Type | Description |
|-------|------|-------------|
| rule_candidates | list[RuleTenderCandidate] | From rule scan |
| tender_matches | list[dict] | Threshold-passing tender items |
| tender_stretch | list[dict] | Stretch bucket |
| permit_matches | list[dict] | Permit items |
| permit_stretch | list[dict] | Permit stretch |
| award_matches | list[dict] | Construction awards |
| top | list[dict] | Assembled final matches (≤ limit) |
| total_candidates | int | Pre-assembly count |

### SessionPhaseMetrics

Logged per request (observability only).

| Field | Type | Description |
|-------|------|-------------|
| read_ms | float | B1/C1 duration |
| hybrid_write_ms | float | B3/C3 duration |
| final_db_ms | float | B5/C6 duration |
| db_total_ms | float | Sum of DB phases |
| cpu_total_ms | float | Wall time minus db_total |

## State Transitions

```text
[Request] → Read (session open)
         → ReadBundle (session closed)
         → CPU: rule scan + top-20 pick
         → Hybrid Write (session open) → HybridWriteResult (session closed)
         → CPU: rule-to-items + permits + awards + assembly
         → Final DB (session open, ≤15 tenders) → enriched top (session closed)
         → Response JSON (unchanged shape)
```

## Database Tables Touched (unchanged schema)

| Table | Read phase | Write phase |
|-------|------------|-------------|
| companies / arch_companies | Load company by ID | — |
| tenders / commercial_tenders / arch_tenders | Load candidate rows | — |
| permits | Load candidate rows | — |
| contract_awards | Load candidate rows (construction) | — |
| tender_matches | Load fresh cache | Upsert during hybrid write |

## Relationships to Existing Code

- `RuleTenderCandidate` — unchanged dataclass; populated during CPU rule scan from `DiscoveryReadBundle.tender_rows`
- `CompanySignals` — unchanged; built from expunged company in read phase
- Response dict from `_discover_*_opportunities` — identical keys to current implementation
