# Authenticated LinkedIn Research Mode

Standalone research module under `research/linkedin/`. **No Registry Engine. No database writes.**

## First-time setup (5 minutes)

```powershell
cd C:\Users\DAVIDSURF\Projects\bc-tender-scraper
pip install -r research/linkedin/requirements.txt
playwright install chromium
```

## Login (once)

```powershell
python research/linkedin/scripts/login_profile.py
```

1. Chromium opens to LinkedIn login.
2. Log in manually — complete MFA if prompted.
3. Wait until your feed loads (or press Enter when ready).
4. Profile is saved to `research/linkedin/.session/browser_profile/`.

**Never enter username/password in scripts or env vars.** Login is manual only.

Future runs reuse this profile automatically. You will not be asked to log in again unless the profile expires.

## Refreshing the browser profile (under 2 minutes)

If batches fail with “profile expired” or redirect to login:

```powershell
python research/linkedin/scripts/login_profile.py
```

Log in again in the browser window. The same profile directory is updated in place.

To start completely fresh (rare):

```powershell
Remove-Item -Recurse -Force research/linkedin/.session/browser_profile
python research/linkedin/scripts/login_profile.py
```

## Running batches

Default: **50 companies per run**, auto-resume from `progress.json`.

```powershell
python research/linkedin/run_batch.py
```

| Flag | Purpose |
|------|---------|
| `--limit 100` | Process up to 100 companies this run |
| `--offset 200` | Start at queue index 200 (overrides auto-resume) |
| `--refresh` | Re-scrape even when cache exists |
| `--delay 2.5` | Seconds between requests (default 2.0) |
| `--no-headless` | Visible browser for debugging |
| `--use-session-fallback` | Use `LINKEDIN_SESSION_PATH` JSON instead of profile |

### Resume workflow

1. Run `python research/linkedin/run_batch.py`.
2. Check `batch_report.md` for `next_offset` and `remaining`.
3. Run the same command again — it continues from `progress.json`.
4. Repeat until `remaining` is 0.

Interrupting mid-batch is safe. Completed companies are cached; progress is saved after each company.

## Cache management

Per-company JSON files live in:

```
research/linkedin/cache/
  Houle Electric Ltd.json
  Bird Construction.json
  ...
```

- **Exists + no `--refresh`** → skipped (counted as cached).
- **`--refresh`** → re-scraped and cache overwritten.
- Cache is gitignored — local research data only.

To clear one company:

```powershell
Remove-Item "research/linkedin/cache/Company Name.json"
```

To clear all cache:

```powershell
Remove-Item research/linkedin/cache/*.json
Remove-Item research/linkedin/progress.json
```

## Progress tracking

`research/linkedin/progress.json` tracks:

- `companies_completed` / `remaining`
- `next_offset` for resume
- `last_processed` company
- `errors` list (permanent failures kept for review)
- `started_at` / `updated_at` / `finished_at`

## Batch reports

After every batch:

| File | Contents |
|------|----------|
| `batch_report.json` | Processed, cached, failed, new pages, field counts |
| `batch_report.md` | Human-readable summary + next offset |

## Error recovery

- **Transient errors** (timeout, network) → retried up to 2 times, then recorded and batch continues.
- **Permanent errors** (404, page not found) → skipped, cached as error, batch continues.
- **Profile expired** → batch stops with refresh instructions (affects all pages).

One bad company never stops the entire batch.

## Fallback: storageState JSON

Prefer persistent profile. Fallback if needed:

```powershell
python research/linkedin/scripts/create_session.py
$env:LINKEDIN_SESSION_PATH = "C:\...\research\linkedin\.auth\linkedin_session.json"
python research/linkedin/run_batch.py --use-session-fallback
```

Refresh fallback session:

```powershell
python research/linkedin/scripts/create_session.py
```

## Downstream artifacts

Each batch merges cache into `linkedin_companies_raw.json` for compatibility with:

```powershell
python research/linkedin/run_enrichment.py
python research/linkedin/run_validation.py --skip-discover
```

## Safety checklist

| Rule | Status |
|------|--------|
| All data under `research/linkedin/` | Yes |
| No production Postgres | Yes |
| No Registry Engine | Yes |
| No `companies` / `market_registry` writes | Yes |
| Profile + cache gitignored | Yes |
