# LinkedIn Company Discovery — Experimental Research Pipeline

> **Paused (2026-07-06):** Authenticated validation blocked — see [TODO.md](./TODO.md).

**Not part of TenderScope.** No database writes. No Registry Engine. All artifacts stay in `research/linkedin/`.

## Authenticated mode (default)

Persistent browser profile + resumable batches. See **[AUTHENTICATED_MODE.md](./AUTHENTICATED_MODE.md)** for full setup.

```powershell
cd C:\Users\DAVIDSURF\Projects\bc-tender-scraper
pip install -r research/linkedin/requirements.txt
playwright install chromium

# One-time manual login (MFA OK)
python research/linkedin/scripts/login_profile.py

# Verify session before batches
python research/linkedin/scripts/verify_profile.py

# Process 50 companies (auto-resumes from progress.json)
python research/linkedin/run_batch.py
```

Outputs per batch: `progress.json`, `batch_report.json`, `batch_report.md`, `cache/*.json`, merged `linkedin_companies_raw.json`.

## Fallback: storageState session JSON

If you prefer exported cookies instead of a persistent profile:

```powershell
python research/linkedin/scripts/create_session.py
$env:LINKEDIN_SESSION_PATH = "research/linkedin/.auth/linkedin_session.json"
python research/linkedin/run_batch.py --use-session-fallback
```

## Offline / public modes

```powershell
# Offline sample (no network)
python research/linkedin/run_pipeline.py --use-sample

# Public fetch only (low success rate, no login)
python research/linkedin/run_validation.py --min-urls 50 --max-urls 100
```

## Library

Selected: **[joeyism/linkedin_scraper](https://github.com/joeyism/linkedin_scraper)** — see [LIBRARY_EVALUATION.md](./LIBRARY_EVALUATION.md).

## Constraints

- NO `companies` table writes
- NO Registry Engine integration
- NO production database access
- Experimental discovery source only
