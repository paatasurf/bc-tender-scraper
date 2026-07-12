# Phase 1 CI — Developer Guide

## What CI does

On every **pull request** and **push** to `master` (and on manual **workflow_dispatch**), GitHub Actions runs **Quality Gate**:

1. Clean Python 3.12 environment  
2. Install `requirements.txt` + `requirements-dev.txt`  
3. Syntax check (`compileall`)  
4. Import smoke for core packages  
5. Formatting / ruff (advisory in Phase 1)  
6. **Full `tests/unit` suite** — **blocking**  
7. Publishes `reports/ci-report.md` + JUnit + coverage artifacts  

DB integration tests **skip** when `CI=true` and `CI_DATABASE_URL` is unset (no production DB in CI).

## How to run the same checks locally

```powershell
cd C:\Users\DAVIDSURF\Projects\bc-tender-scraper
pip install -r requirements.txt -r requirements-dev.txt

# Match CI: skip local Postgres integration tests
$env:CI = "true"
$env:CI_DATABASE_URL = ""

python -m compileall -q api db pipeline intelligence config scraper
pytest tests/unit -q --tb=short
```

Optional with coverage:

```powershell
pytest tests/unit -q --cov=api --cov=db --cov=pipeline --cov=intelligence --cov-report=term-missing
```

To run DB integration tests locally (needs local Postgres in `.env.local`):

```powershell
Remove-Item Env:CI -ErrorAction SilentlyContinue
pytest tests/unit -q
```

## How developers should use the workflow

1. Open a PR against `master`.  
2. Wait for **Quality Gate** (green check).  
3. Do not merge red PRs.  
4. Download the `ci-reports-scraper-*` artifact if you need the markdown report.  
5. Fix failing **tests** or **code**; do not disable the workflow.

## Files

| Path | Role |
|------|------|
| `.github/workflows/quality-gate.yml` | Pipeline |
| `requirements-dev.txt` | pytest, ruff, coverage |
| `pytest.ini` | pytest defaults |
| `ruff.toml` | critical lint rules |
| `scripts/ci_report_summary.py` | Report generator |
| `docs/engineering/` | Full engineering docs |
