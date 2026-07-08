# LinkedIn curated verification — paused

**Status:** Paused (2026-07-06). Implementation is committed; authenticated validation is blocked.

## Remaining blockers

### 1. Profile lock

The persistent browser profile (`research/linkedin/.session/browser_profile/`) is locked while any Chromium window from `login_profile.py` remains open. Playwright then cannot read `Default/Network/Cookies` or other profile files (WinError 32 / access denied).

**Fix:** Close all Playwright/Chromium windows opened for login before running `verify_profile.py` or `run_curated_verification.py`.

### 2. Chrome / Playwright version mismatch

Login uses Playwright’s bundled Chromium; subsequent runs may hit `Failed to perform User Data migration following a Chrome version downgrade` when profile data was written by a different Chromium build.

**Fix:** Re-login after `playwright install chromium`, or pin Playwright + Chromium versions in `research/linkedin/requirements.txt` and wipe `.session/browser_profile/` before a fresh login.

### 3. Session not reusable in automation

Even after manual login reported success, automated runs redirect to `/uas/login` instead of `/feed/`. The saved profile does not authenticate headless (or visible) Playwright sessions.

**Fix:** Investigate `li_at` cookie persistence, anti-bot detection, and whether login_profile should re-open the profile to self-verify before exit (see `scripts/verify_profile.py`).

## Resume checklist

1. Close all LinkedIn login browser windows.
2. `python research/linkedin/scripts/login_profile.py` — log in, reach feed, close browser.
3. `python research/linkedin/scripts/verify_profile.py` — must print `OK: authenticated feed reachable`.
4. `python research/linkedin/run_curated_verification.py --classes A --with-website-only --limit 20`
5. Only then run full Class A/B batch.
