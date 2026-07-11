# Phase 1 CI — Compatibility Review

**Date:** 2026-07-10  
**Status:** Review complete — proceed with **repository-specific** CI only  
**Rule:** No business-logic or architecture changes

---

## Recommendation: repository-specific CI (not centralized)

| Option | Verdict |
|--------|---------|
| **Centralized monorepo / umbrella CI** | Reject — three separate GitHub remotes (`paatasurf/bc-tender-scraper`, `tenderscope-kg`, `voice-n8n-agent`), different Python packaging, different Railway services |
| **Reusable workflow in one repo called by others** | Deferred — adds cross-repo coupling and secret/permission complexity |
| **Identical per-repo workflows + shared docs** | **Adopt** — safest; each Railway service gated by its own repo’s checks |

Future repos copy the workflow template from `docs/engineering/CI_WORKFLOW_TEMPLATE.md` without redesign.

---

## Compatibility matrix

| System | Interference risk | Finding | Action |
|--------|-------------------|---------|--------|
| **Railway** | **High if misconfigured** | Today: push/merge to `master` auto-deploys via Railway. GitHub Actions alone **does not** block Railway. | **Required safeguard (ops, not code):** (1) GitHub branch protection: require `quality-gate` status check before merge to `master`. (2) Railway → enable **Wait for CI** / check suites for the GitHub repo (dashboard). Do **not** change `railway.toml` start commands. |
| **n8n** | None | n8n calls production HTTP `/internal/*` after deploy. CI does not alter routes or keys. | No change |
| **bc-tender-scraper** | Low | CI runs unit tests offline; `db_test_safety` refuses production URLs. No `DATABASE_URL` in CI secrets for prod. | Add workflows + `requirements-dev.txt` only |
| **tenderscope-kg** | Low | Already has pytest/ruff in `pyproject.toml` `[dev]`. Graph is projection, not SoR. | Add workflow only |
| **voice-n8n-agent** | Low | Control plane / memory / narrator tests are in-repo. CI must not set production DB URLs. | Add workflow + `requirements-dev.txt` |
| **Graph** | None | CI does not mutate `.tkg/graph.db` or production graph DB | No change |
| **Registry / merge / resolution** | None | Tests are unit/mocked; no Class C/D apply in CI | No change |
| **Session / Strategic Memory / Narrator / EDE** | None | voice-agent unit tests only; feature flags unchanged | No change |
| **Existing deploy process** | **Process change only** | Deploy path remains: merge to `master` → Railway. Quality gate sits **before** merge (and optionally Railway waits). | Document; do not redesign Nixpacks/Docker |

---

## Pre-existing blockers discovered

1. **`tests/unit/competitive_fixtures.py`** missing required `sector_confidence` on `CompanyIntelligenceProfile` → competitive threat tests fail.  
   - **Fix allowed:** test fixture only (not business logic). **Done.**
2. Full unit suite may include DB-dependent tests that **skip** without local Postgres — acceptable in CI.  
3. **crawlee** in scraper `requirements.txt` makes CI install heavier — keep (needed for imports); do not remove from prod deps.
4. **Ruff F821 debt** in scraper `pipeline/` (e.g. missing imports / forward refs) — **lint is advisory (`continue-on-error`) in Phase 1** so the test gate can ship; promote lint to blocking after a dedicated cleanup PR (no business-logic changes).

---

## Safeguards required before claiming “deploy fails if tests fail”

Without these, CI is advisory only:

1. GitHub: protect `master` — require status check `Quality Gate`.  
2. Railway: Wait for CI / required check suites enabled per service.  
3. Never store `DATABASE_URL_PRODUCTION` in GitHub Actions secrets for this Phase 1 gate.

---

## Proceed / stop decision

| Decision | Result |
|----------|--------|
| Architecture change? | No |
| Business logic change? | No (fixture repair only) |
| Deployment redesign? | No |
| Blocking compatibility issue? | Railway wait-for-CI is **ops configuration**, documented above — not a code stop |
| **Proceed with implementation?** | **Yes** |
