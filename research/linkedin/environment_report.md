# LinkedIn Research Pipeline — Environment Diagnostic Report

Generated: 2026-07-06T02:30:00+00:00

> Research only. No Registry or production DB writes.

## Interpreter used by research scripts

Both `research/linkedin/run_batch.py` and `research/linkedin/run_validation_500.py` use:

```text
#!/usr/bin/env python3
```

On this machine that resolves to the system Python on PATH (not a project virtualenv).

| Field | Value |
|-------|-------|
| **Python executable** | `C:\Python313\python.exe` |
| **Python version** | 3.13.5 (tags/v3.13.5:6cb20a2, Jun 11 2025, 16:15:46) [MSC v.1943 64 bit (AMD64)] |
| **pip executable** | `C:\Python313\Scripts\pip.exe` |
| **pip version** | 25.1.1 |
| **Virtual environment** | **None** (system Python + user site-packages) |
| **sys.prefix** | `C:\Python313` |
| **User site-packages** | `C:\Users\DAVIDSURF\AppData\Roaming\Python\Python313\site-packages` |

### Note on install location

`linkedin-scraper` was **missing** before this repair. It was installed with:

```powershell
python -m pip install "linkedin-scraper>=3.1.2" "playwright>=1.40.0"
```

Because `C:\Python313\Lib\site-packages` is not writeable without elevation, pip used **user site-packages** (`--user` default). Imports succeed because that path is on `sys.path`.

---

## Required packages

| Package | Import test | Installed version | Location |
|---------|-------------|-------------------|----------|
| linkedin_scraper | **PASS** | 3.1.2 | user site-packages |
| playwright | **PASS** | 1.60.0 | user site-packages |
| pydantic | **PASS** | 2.13.4 | user site-packages |
| beautifulsoup4 (bs4) | **PASS** | 4.15.0 | user site-packages |
| lxml | **PASS** | 5.4.0 | user site-packages |
| aiohttp | **PASS** | 3.14.0 | user site-packages |
| requests | **PASS** | 2.34.2 | user site-packages |

---

## linkedin_scraper class imports

| Import | Result |
|--------|--------|
| `from linkedin_scraper import CompanyScraper` | **PASS** |
| `from linkedin_scraper import BrowserManager` | **PASS** |
| `import linkedin_scraper` (version) | **PASS** — 3.1.2 |

---

## Playwright

| Check | Result |
|-------|--------|
| **Playwright Python package** | 1.60.0 (`python -m playwright --version` → Version 1.60.0) |
| **linkedin-scraper requires** | `playwright>=1.40.0` — **compatible** |
| **Chromium installed** | **Yes** |
| **Browser directory** | `C:\Users\DAVIDSURF\AppData\Local\ms-playwright` |
| **Chromium launch test** | **PASS** (headless `about:blank`) |

Chromium was (re)installed with:

```powershell
python -m playwright install chromium
```

---

## Batch runner dependency gate

```python
from research.linkedin.batch_runner import ensure_scraper_dependencies
ensure_scraper_dependencies()
```

**Result: PASS**

---

## Root cause of prior batch failures

The authenticated batch (`ok=0, failed=50+`) failed with:

```text
No module named 'linkedin_scraper'
```

`linkedin-scraper` was listed in `research/linkedin/requirements.txt` but had **never been installed** into the interpreter used to run `run_validation_500.py` / `run_batch.py`.

---

## Overall status

| Check | Status |
|-------|--------|
| Python interpreter identified | **PASS** |
| All required packages import | **PASS** |
| linkedin_scraper imports | **PASS** |
| Playwright + Chromium | **PASS** |
| `ensure_scraper_dependencies()` | **PASS** |

## **FINAL: PASS**

Environment is ready for dependency-level validation. Next step (separate task): smoke-test authenticated scraping against known-good LinkedIn company URLs before scaling to 500 companies.
