# Phase 1 — Validation Checklist

Use before merging the CI infrastructure PR(s) and before enabling branch protection.

## Compatibility (must be YES)

- [ ] No business logic / scoring / Registry changes in the CI PR
- [ ] No Railway startCommand / Dockerfile redesign
- [ ] No n8n workflow edits
- [ ] No production secrets in GitHub Actions
- [ ] Compatibility review read: `CI_COMPATIBILITY_REVIEW.md`

## bc-tender-scraper

- [ ] `.github/workflows/quality-gate.yml` present
- [ ] `requirements-dev.txt`, `pytest.ini`, `ruff.toml` present
- [ ] `tests/unit/competitive_fixtures.py` includes `sector_confidence`
- [ ] Local: `pytest tests/unit/test_ci_regression_contracts.py tests/unit/test_competitive_threat_score.py tests/unit/test_db_safety.py -q` passes
- [ ] Local: critical suites listed in workflow pass
- [ ] Workflow appears under Actions after push

## tenderscope-kg

- [ ] `.github/workflows/quality-gate.yml` present
- [ ] Local: `pip install -e ".[dev,rest]"` then `pytest tests -q` passes
- [ ] Workflow appears under Actions after push

## voice-n8n-agent

- [ ] `.github/workflows/quality-gate.yml` present
- [ ] `requirements-dev.txt`, `pytest.ini`, `ruff.toml` present
- [ ] Local: `pytest tests -m "not integration" -q` passes (or known skips only)
- [ ] Workflow appears under Actions after push

## Ops gate (deploy actually blocked on red CI)

- [ ] GitHub branch protection requires **Quality Gate** on `master` (each repo)
- [ ] Railway Wait-for-CI enabled (each service)
- [ ] Test: open PR with deliberate failing assert → merge blocked
- [ ] Test: green PR merges → Railway deploys

## Reporting

- [ ] Artifact `ci-reports-*` contains `deployment-readiness.md`
- [ ] Readiness shows pass/fail counts and coverage summary
