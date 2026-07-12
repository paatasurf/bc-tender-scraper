# Railway — Wait for CI

**Service:** `bc-tender-scraper`  
**Project:** honest-creativity  
**Repo:** `paatasurf/bc-tender-scraper`  
**Deploy branch:** `master`

## Required setting

In Railway dashboard:

1. Open service **bc-tender-scraper** → **Settings** → **Source** / **GitHub**  
2. Confirm connected repo + branch `master`  
3. Enable **Wait for CI**  
4. Ensure GitHub App permissions for Railway are accepted (Check suites / commit statuses)

## Expected behavior

| GitHub CI | Railway |
|-----------|---------|
| Workflows running | Deployment **WAITING** |
| Quality Gate **fails** | Deployment **SKIPPED** (no production release) |
| Quality Gate **passes** | Deployment proceeds (build → healthcheck) |

## Requirements (Railway docs)

- Workflow exists in the repo  
- Workflow runs on **`push`** (our `quality-gate.yml` includes `push: branches: [master]`)

## CLI note

Railway CLI (v5) does not expose a documented `wait for CI` toggle. Enable in the dashboard; record confirmation in the final validation report.

## Verification

1. Push a commit that fails Quality Gate on `master` (via PR merge blocked — prefer PR path)  
2. Or open PR that fails CI — with Wait for CI, a bad merge cannot reach master; if somehow pushed, Railway must SKIP  
3. Green merge → Railway deploys new revision  
