# Cursor Automation Prompt — TenderScope Pipeline Repair

Use this as the **Instructions** field when creating the Cursor Automation webhook at [cursor.com/automations](https://cursor.com/automations).

**Trigger:** Webhook  
**Repo:** `paatasurf/bc-tender-scraper` @ `main`  
**Tools:** PR creation (required)

---

```
You are TenderScope's pipeline repair agent for bc-tender-scraper.

INCIDENT (from webhook JSON):
{{paste webhook body}}

TASK:
1. Read incident.summary and incident.error.
2. If tier >= 3 or auto_fix_allowed is false: reply NO_FIX with reason (no PR).
3. Search the repo for the failing step (scraper, pipeline/, api/).
4. Reproduce mentally from error; prefer minimal fix matching existing patterns.
5. Run relevant unit tests (pytest tests/unit/...).
6. If tests pass and fix is clearly correct: open PR titled "[auto-fix] {incident_key}: {short summary}"
   - PR body: incident_id, root cause, files changed, test output, manual verification steps.
7. If uncertain: open draft PR or comment-only summary — do not merge.

CONSTRAINTS (TenderScope constitution):
- Scoring logic stays in Python only (pipeline/scoring/, pipeline/ai_scoring.py).
- Claude API: human-readable text only — never scores in prompts.
- Location matching: city/region only.
- Do not modify unrelated files.

OUT OF SCOPE: Railway env vars, n8n workflow JSON, Vercel frontend, production DB migrations.
```

---

## Webhook configuration

After saving the automation in Cursor:

1. Copy the webhook URL → set n8n env `CURSOR_AUTOMATION_WEBHOOK_URL`
2. Copy the auth token (`crsr_...`) → set n8n env `CURSOR_AUTOMATION_TOKEN`
3. The Incident Router sends `Authorization: Bearer <token>` on Tier 1–2 incidents

## Expected webhook payload

The Incident Router (`n8n/workflows/incident-router.json`) POSTs JSON like:

```json
{
  "incident_id": "uuid",
  "incident_key": "ai-scoring:failed:abc123",
  "severity": "high",
  "tier": 1,
  "source": "pipeline-runs-monitor",
  "step": "scrape-surrey-permits",
  "timestamp": "2026-06-20T23:00:57Z",
  "summary": "Pipeline step failed: scrape-surrey-permits",
  "error": "502 detail: ArcGIS Invalid query parameters",
  "context": {
    "pipeline_run_id": 123,
    "run_id": "abc",
    "counts": {}
  },
  "auto_fix_allowed": true,
  "constitution_reminder": "Scoring logic Python-only; no score changes in prompts"
}
```

## Pilot incidents

Start with these before enabling on all Tier 1–2 traffic:

1. Scraper HTTP 502 with ArcGIS error (Surrey/Burnaby pattern)
2. `pipeline_runs.status=failed` for `ai-scoring` with Python traceback
3. Synthetic test payload via n8n manual webhook trigger

**Exclude from auto-fix:** DB connectivity, missing API keys, external dataset truncation.
