# Validation notes (feature 004)

**Date**: 2026-06-16

## Local unit tests (quickstart §1)

```
pytest tests/unit/ -v
```

Result: **30 passed, 2 skipped** (baseline JSON fixtures not yet captured from production)

## Baseline capture (quickstart §2)

Production timed out during capture (`scripts/capture_opportunities_baselines.py`).
Re-run after deploy when opportunities endpoint responds.

## Post-deploy checks

1. `python scripts/verify_company_opportunities_deploy.py`
2. `python scripts/verify_opportunities_concurrent.py https://<railway-domain>`
3. Confirm logs include `db_phases_total=` under 10s per request

## Constitution (CC-001–CC-005)

- Scoring math unchanged; only session timing refactored
- No new LLM calls; hybrid path remains deterministic Python
- API response keys unchanged per `contracts/opportunities-discovery-response.json`
- Metrics logged only; not added to response body
