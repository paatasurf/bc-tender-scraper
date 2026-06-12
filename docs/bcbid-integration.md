# BC Bid Integration — Workflow & Validation Report

Generated as part of BC Bid enablement. Re-run validation:

```bash
python scripts/validate_bcbid_integration.py
python scripts/validate_bcbid_integration.py --live   # requires cookies
```

## Architecture

### Daily pipeline (APScheduler)

```
api.main lifespan → start_scheduler()
  job: daily_scrape_import (06:00 America/Vancouver)
    → run_pipeline.py
      → scraper.main.run()
        Step 1: Federal + BC Bid tenders (run_federal_scraper)
        Step 2–7: MERX, commercial, permits, signals
      → import_all_csvs()
      → AI scoring, company intelligence
```

### n8n workflows

**Option A — Combined (recommended, matches existing scrape-federal node)**

| Order | Method | Step name | Notes |
|------:|--------|-----------|-------|
| 1 | POST | `/internal/scrape/federal` | Federal + BC Bid merged to `tenders.csv` |
| 2 | POST | `/internal/scrape/merx-arch` | |
| 3 | POST | `/internal/scrape/commercial` | |
| 4 | POST | `/internal/import` | Upserts `tenders` table |

**Option B — Dedicated BC Bid retry**

| Order | Method | Step name | Notes |
|------:|--------|-----------|-------|
| 1 | POST | `/internal/scrape/federal` | Federal only if BC Bid cookies expired on combined run |
| 2 | POST | `/internal/scrape/bcbid` | Refresh provincial rows; preserves federal from CSV |
| 3 | POST | `/internal/import` | |

## Authentication

| Local | Production |
|-------|------------|
| `bcbid_cookies.txt` (Netscape, gitignored) | `BCBID_COOKIES_CONTENT` Railway variable |

Export cookies after passing the BC Bid browser check at `https://www.bcbid.gov.bc.ca`.

## Disable switch

Set `PIPELINE_SKIP_BCBID=true` to skip provincial scrape while keeping federal tenders.

## Expected production impact

| Metric | Before | After (with valid cookies) |
|--------|-------:|---------------------------:|
| `/api/tenders` | 13 | ~223–276 |
| BC Bid rows (`source=bcbid.gov.bc.ca`) | 0 | ~210–263 |
| Federal rows preserved | 13 | ~13 |

See `bcbid-integration-validation.json` for latest automated check results.
