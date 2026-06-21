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

## Setup

### 1. Environment variables

| Variable | Description |
|----------|-------------|
| `TELEGRAM_CHAT_ID` | Telegram chat for incident alerts |
| `INCIDENT_ROUTER_WEBHOOK_URL` | Production URL from Incident Router webhook node |
| `CURSOR_AUTOMATION_WEBHOOK_URL` | Webhook URL from Cursor Automation (cursor.com/automations) |
| `CURSOR_AUTOMATION_TOKEN` | Bearer token (`crsr_...`) for Cursor webhook auth |

### 2. Import order

1. Import `incident-router.json` → activate → copy Production webhook URL → set `INCIDENT_ROUTER_WEBHOOK_URL`.
2. Import `error-workflow.json` → note workflow ID.
3. Import `pipeline-runs-monitor.json`.
4. Re-import or update `ai-scoring-poll.json` if already deployed.

### 3. Credentials

Attach Telegram Bot credentials to Telegram nodes (replace `REPLACE_WITH_YOUR_TELEGRAM_CREDENTIAL_ID`).

### 4. Error Workflow assignment

On **AI Scoring**, **Bulk Prescore**, **Pipeline Runs Monitor**, and **Incident Router**:  
Settings → Error Workflow → **TenderScope — Global Error Handler**.

Optionally set the instance default Error Workflow in n8n Settings.

### 5. Activate

- **Incident Router** (webhook must be live)
- **Pipeline Runs Monitor**

Error Handler activates automatically when assigned as Error Workflow.

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
