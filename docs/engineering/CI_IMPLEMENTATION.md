# Phase 1 — Engineering Quality Gate (Implementation)

## Decision: repository-specific CI

Each production GitHub repository owns its own `Quality Gate` workflow:

| Repository | Workflow | Default branch |
|------------|----------|----------------|
| `bc-tender-scraper` | `.github/workflows/quality-gate.yml` | `master` |
| `tenderscope-kg` | `.github/workflows/quality-gate.yml` | `master` |
| `voice-n8n-agent` | `.github/workflows/quality-gate.yml` | `master` |

**Not centralized:** separate remotes, packaging, and Railway services. Shared pattern is documented in `CI_WORKFLOW_TEMPLATE.md` for future repos.

Compatibility review: [`CI_COMPATIBILITY_REVIEW.md`](./CI_COMPATIBILITY_REVIEW.md).

---

## What runs (blocking)

On **pull_request** to `master`, **push** to `master`, and **workflow_dispatch**:

### bc-tender-scraper
- Ruff critical lint (`E9/F63/F7/F82`) — **advisory in Phase 1** (`continue-on-error`) until F821 debt cleared
- Production safety tests (`test_db_safety*`, startup)
- Company resolution + canonical merge suites
- Competitive intelligence + unified/BP-related scoring suites
- API/lifecycle/enrichment + `test_ci_regression_contracts.py`
- Full `tests/unit` with coverage XML + JUnit
- Artifact: `reports/deployment-readiness.md`

### tenderscope-kg
- Ruff check + format
- Full `tests/` (graph, `company_uid` contracts, EDE/OIE)
- Coverage + readiness report

### voice-n8n-agent
- Ruff critical lint
- Company resolution / control plane / EDE
- Memory, narrator, evidence_hash / cache suites
- Full `tests` with `-m "not integration"`
- Coverage + readiness report

---

## What does NOT change

- Business logic / scoring / Registry Constitution
- Railway `startCommand` / Dockerfiles / Nixpacks
- n8n workflows
- Production database URLs (never set in CI)

---

## Required ops follow-up (deploy actually waits)

CI alone is advisory until:

1. **GitHub branch protection** on `master` for each repo: require status check **Quality Gate**.
2. **Railway → Wait for CI** (check suites) enabled for each service linked to that GitHub repo.

See [`CI_DEPLOYMENT.md`](./CI_DEPLOYMENT.md) and [`CI_ROLLBACK.md`](./CI_ROLLBACK.md).

---

## Local reproduction

```powershell
# scraper
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/unit -q --cov=api --cov=db --cov=pipeline --cov=intelligence

# kg
pip install -e ".[dev,rest]"
pytest tests -q

# voice
pip install -r requirements.txt -r requirements-dev.txt
pytest tests -m "not integration" -q
```
