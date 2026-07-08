# LinkedIn Company Scraper — Library Evaluation (2026)

**Scope:** Experimental discovery research only. Not integrated with TenderScope Registry Engine.

## Candidates reviewed

| Library | GitHub | Last activity | Stars | Company pages | Python | Batch | Notes |
|---------|--------|---------------|------:|---------------|--------|-------|-------|
| **joeyism/linkedin_scraper** | [github.com/joeyism/linkedin_scraper](https://github.com/joeyism/linkedin_scraper) | 2026-04-10 (v3.1.2) | ~4,300 | `CompanyScraper` + Pydantic `Company` model | Async Playwright | Yes (URL list) | Largest community; v3 rewrite |
| **vertexcover-io/linkedin-spider** | [github.com/vertexcover-io/linkedin-spider](https://github.com/vertexcover-io/linkedin-spider) | 2026-05-12 (v0.3.0) | ~25 | `scrape_company()` | Sync + CLI/MCP | Yes | Modern anti-detection; small community |
| **tomquirk/linkedin-api** | [github.com/tomquirk/linkedin-api](https://github.com/tomquirk/linkedin-api) | Stale (~2023) | ~1,200 | Company endpoints | Requests | Limited | Unofficial API; breaks often |
| **Selenium + BeautifulSoup (DIY)** | N/A | N/A | N/A | Custom | Selenium | Manual | High maintenance; no shared schema |

## Selected library: **joeyism/linkedin_scraper** (`pip install linkedin-scraper`)

### Why this one

1. **Actively maintained in 2026** — v3.1.2 released April 2026; Playwright rewrite addresses Selenium fragility.
2. **Strongest GitHub community** — ~4,300 stars, 30+ contributors, 140 open issues with ongoing responses; far more battle-tested than newer alternatives.
3. **First-class company scraping** — dedicated `CompanyScraper`, validated `Company` Pydantic model with `name`, `website`, `industry`, `headquarters`, `company_size`, `specialties`, `founded`, `about_us`.
4. **Python-native async batch** — iterate a URL list with shared browser session; fits discovery pipelines.
5. **Stable data contract** — `model_dump()` JSON output; easier to normalize and compare offline.

### Why not linkedin-spider

- Only ~25 stars despite recent releases — limited community signal for debugging LinkedIn DOM changes.
- Broader surface area (DMs, connections) adds scope we do not need for company discovery.
- Still viable as a fallback; documented here but not wired into this pipeline.

### Why not tomquirk/linkedin-api

- Effectively unmaintained; relies on reverse-engineered private endpoints that LinkedIn rotates frequently.
- Higher account-ban risk without the browser realism Playwright provides.

### Operational requirements (all options)

- LinkedIn **authenticated session** (saved cookies / session JSON) for anything beyond trivial volume.
- Rate limiting and residential-style access patterns; datacenter IPs block quickly.
- LinkedIn ToS restricts automated scraping — this pipeline is **local research only**, no production use.

## Integration in this repo

- Optional dependency: `research/linkedin/requirements.txt` (`linkedin-scraper`, `playwright`).
- Adapter: `research/linkedin/scraper/adapter.py` wraps `CompanyScraper`.
- Offline validation: `--use-sample` bypasses live scraping for normalize/compare/report testing.
