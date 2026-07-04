# Registry Constitution

Governing principles for TenderScope company registry, canonical merge, and
enterprise seed work.

## 1. Production database safety (non-negotiable)

See [PRODUCTION_SAFETY.md](./PRODUCTION_SAFETY.md).

## Script classification

See [../scripts/CLASSIFICATION.md](../scripts/CLASSIFICATION.md) for the full
script registry (Classes A–D), runtime escalation rules, and Class C dry-run
validity requirements.

- CLI scripts default to **local** Postgres via `DATABASE_URL` in `.env.local`.
- Production Railway URLs live in `DATABASE_URL_PRODUCTION`, never as the default
  `DATABASE_URL` for scripts.
- Any host matching `*.proxy.rlwy.net` is **PRODUCTION** — no exceptions.
- Destructive operations (`init_db()`, migrations, merge `--apply`, backfills)
  **must** pass `db/db_safety.py` and require `--allow-production` plus an
  interactive confirmation phrase before modifying production.
- Every authorized production write is logged to `logs/destructive_operations.log`.

## 2. Deterministic registry logic

- Company canonical merge, alias resolution, and tier assignment are **Python-only**.
- No LLM may create, merge, or score registry entities.
- Merge plans must be reproducible from the same DB snapshot (dry-run before apply).

## 3. Transparent provenance

- Canonical rows record `entity_role`, `canonical_merge_method`, and rollback snapshots.
- Never blend construction hybrid scores with BD pursuit scores in registry outputs.

## 4. City-level geography only

- Location matching and registry enrichment use city/region — never street address.

## 5. Dry-run before apply

- Any bulk registry mutation requires a dry-run report committed to `exports/` and
  human review before `--apply`.
