# TenderScope n8n Monitoring Workflows

Import JSON files from `n8n/workflows/` into your n8n instance.

## Workflows

| Workflow | File | Purpose |
|----------|------|---------|
| Incident Router | `incident-router.json` | Normalize, dedupe (6h), classify Tier 1/2/3, Telegram + Cursor webhook |
| Global Error Handler | `error-workflow.json` | Catches n8n failures from assigned workflows |
| Pipeline Runs Monitor | `pipeline-runs-monitor.json` | Polls `GET /internal/runs?limit=20` every 15 minutes |
| AI Scoring (sync) | `ai-scoring-poll.json` | Existing; failures route to Incident Router |
| Bulk Prescore | `bulk-prescore.json` | Existing; assign Error Workflow |
| Lifecycle Resolver Nightly | `lifecycle_resolver.json` | Daily 06:00 Vancouver: POST `/internal/lifecycle/resolve` |
| Google Enrichment Daily | `google_enrichment.json` | Daily 06:45 Vancouver: POST `/internal/google-enrichment/run` (production batch) |
| Google Enrichment Dry Run | `google_enrichment_dry_run.json` | Manual smoke test: `dry_run=true`, default 10 companies |

## Setup

### 1. Environment variables

| Variable | Description |
|----------|-------------|
| `INTERNAL_API_KEY` | Same key as Railway FastAPI — required for `/internal/google-enrichment/run` |
| `TELEGRAM_CHAT_ID` | Telegram chat for incident alerts |
| `INCIDENT_ROUTER_WEBHOOK_URL` | Production URL from Incident Router webhook node |
| `CURSOR_AUTOMATION_WEBHOOK_URL` | Webhook URL from Cursor Automation (cursor.com/automations) |
| `CURSOR_AUTOMATION_TOKEN` | Bearer token (`crsr_...`) for Cursor webhook auth |

Railway (FastAPI service — not n8n):

| Variable | Default | Purpose |
|----------|---------|---------|
| `GOOGLE_PROVIDER` | `apify` | Primary lookup provider |
| `GOOGLE_PROVIDER_FALLBACK` | `none` | Failover when Apify unhealthy |
| `APIFY_TOKEN` | — | Apify API token (required for live lookups) |
| `APIFY_ACTOR_ID` | `compass/google-maps-extractor` | Configurable actor |
| `GOOGLE_ENRICHMENT_BATCH_SIZE` | `21` | Companies per daily run |
| `GOOGLE_ENRICHMENT_STALE_DAYS` | `30` | Refresh interval for linked Place IDs |

### 2. Import order

1. Import `incident-router.json` → activate → copy Production webhook URL → set `INCIDENT_ROUTER_WEBHOOK_URL`.
2. Import `error-workflow.json` → note workflow ID.
3. Import `pipeline-runs-monitor.json`.
4. Re-import or update `ai-scoring-poll.json` if already deployed.
5. Import `google_enrichment_dry_run.json` → run manually after deploy + `APIFY_TOKEN` → inspect `counts.companies`.
6. Import `google_enrichment.json` → assign Error Workflow → attach Telegram credential → **do not activate** until dry run passes.

### 3. Credentials

Attach Telegram Bot credentials to Telegram nodes (replace `REPLACE_WITH_YOUR_TELEGRAM_CREDENTIAL_ID`).

### 4. Error Workflow assignment

On **AI Scoring**, **Bulk Prescore**, **Pipeline Runs Monitor**, **Google Enrichment Daily**, and **Incident Router**:  
Settings → Error Workflow → **TenderScope — Global Error Handler**.

Optionally set the instance default Error Workflow in n8n Settings.

### 5. Activate

- **Incident Router** (webhook must be live)
- **Pipeline Runs Monitor**
- **Google Enrichment Daily** (after dry run + Apify token verified)

Error Handler activates automatically when assigned as Error Workflow.

### Google Enrichment go-live

1. Deploy FastAPI with `POST /internal/google-enrichment/run` + migration 013.
2. Set `APIFY_TOKEN` on Railway.
3. n8n: run **Google Enrichment Dry Run** manually — check `counts.companies[]` for confidence/snapshots.
4. n8n: activate **Google Enrichment Daily** (06:45 Vancouver).
5. Ops: `GET /internal/google-enrichment/metrics` and `GET /internal/runs?step=google-enrichment`.

### 6. Cursor Automation

Create a Cursor Automation with webhook trigger bound to `paatasurf/bc-tender-scraper`.  
Use the prompt in `docs/runbooks/cursor-automation-prompt.md`.

## Incident flow

```
Error / failed run / AI scoring failure
  → Incident Router webhook
  → Normalize → Dedupe (6h) → Classify tier
  → Telegram (always)
  → Cursor webhook (Tier 1–2 only)
```

`/internal/runs` is a public GET endpoint (no `ALLOW_MANUAL_PIPELINE` required).

## Tier rules

| Tier | Examples | Cursor auto-fix |
|------|----------|-----------------|
| 1 | ArcGIS errors, scraper field renames, Python tracebacks | Yes |
| 2 | Backlog anomalies, import conflicts | Yes (investigate) |
| 3 | DB down, missing API keys, external data loss | No — Telegram only |
