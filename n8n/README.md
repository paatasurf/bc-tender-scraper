# TenderScope n8n Monitoring Workflows

Import JSON files from `n8n/workflows/` into your n8n instance.

## Phase 1 — Observability

| Workflow | File | Purpose |
|----------|------|---------|
| Global Error Handler | `error-workflow.json` | Catches failures from any workflow assigned as its Error Workflow |
| Pipeline Runs Monitor | `pipeline-runs-monitor.json` | Polls `GET /internal/runs?limit=20` every 15 minutes |

### Setup

1. Import `error-workflow.json` and note the workflow ID.
2. Import `pipeline-runs-monitor.json`.
3. Set n8n environment variables:
   - `TELEGRAM_CHAT_ID` — your Telegram chat ID
4. Attach Telegram Bot credentials to Telegram nodes (replace `REPLACE_WITH_YOUR_TELEGRAM_CREDENTIAL_ID`).
5. On **AI Scoring**, **Bulk Prescore**, and **Pipeline Runs Monitor** workflows: Settings → Error Workflow → select **TenderScope — Global Error Handler**.
6. Optionally set the instance default Error Workflow in n8n Settings.
7. Activate **Pipeline Runs Monitor**.

`/internal/runs` is a public GET endpoint (no `ALLOW_MANUAL_PIPELINE` required).
