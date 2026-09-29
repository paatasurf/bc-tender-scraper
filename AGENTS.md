# AGENTS.md

## Cursor Cloud specific instructions

This repo is a Python 3.12 **FastAPI backend** ("BC Construction Data API") backed by
**PostgreSQL**. It scrapes BC construction tenders/permits/signals into Postgres and serves
them over a REST API. The `v0-construction-dashboard/` directory is empty (the frontend is
not committed), so the in-scope product is the Python API + scraper/pipeline.

### Services / how to run

- **API (dev):** `.venv/bin/uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload`
  (Procfile/`railway.toml` use the same `uvicorn api.main:app` command without `--reload`).
  Swagger UI at `/docs`; health at `/api/health`; data at `/api/stats`, `/api/tenders`,
  `/api/arch-tenders`, `/api/permits`, etc.
- **Pipeline/scrapers:** `python run_pipeline.py` and the `run_*.py` helpers. These hit live
  external sites (CanadaBuys, MERX, Vancouver open data, Reddit, etc.) and may be slow/flaky;
  avoid running the full pipeline unless needed.
- **Internal trigger endpoints** (`POST /internal/import`, `/internal/scrape/*`, etc.) are
  gated behind `ALLOW_MANUAL_PIPELINE=true`. The router prefix is `/internal` (NOT
  `/api/internal`).

### Non-obvious setup caveats (the update script does NOT do these)

- **PostgreSQL is a system service that does not auto-start.** Start it each session with:
  `sudo pg_ctlcluster 16 main start`. A `bc` role (password `bc`) and `bc_tenders` database
  already exist in the snapshot.
- **`.env` is gitignored** and lives only in the VM snapshot. It points
  `DATABASE_URL=postgresql://bc:bc@localhost:5432/bc_tenders` and sets `SCHEDULER_ENABLED=false`
  (the daily APScheduler job runs the live scraping pipeline, so keep it off in dev).
- **Seed the DB** (idempotent upsert from the committed CSVs) with `python import_db.py`. This
  also runs schema migrations (`init_db`). Building permits (~50k rows) are a full refresh.
- `ANTHROPIC_API_KEY` is unset; AI scoring/matching endpoints need it but core data
  CRUD/query endpoints work without it.

### Lint / tests / build

- No linter config, no test suite, and no build step exist in this repo. "Build" = installing
  deps + running `uvicorn`. `scripts/smoke_discovery.py` is the closest thing to a smoke test.
