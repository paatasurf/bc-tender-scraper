# Phase 1 — Repository Integration Diagram

```text
                    ┌─────────────────────────────────────┐
                    │           GitHub (3 remotes)         │
                    │                                     │
                    │  bc-tender-scraper   ── Quality Gate │
                    │  tenderscope-kg      ── Quality Gate │
                    │  voice-n8n-agent     ── Quality Gate │
                    └──────────────┬──────────────────────┘
                                   │
                    branch protection requires green gate
                                   │
                                   ▼
                    ┌─────────────────────────────────────┐
                    │         Railway (3 services)         │
                    │   Wait for CI  →  build  →  deploy   │
                    │                                     │
                    │  scraper  : uvicorn api.main:app     │
                    │  voice    : Dockerfile /health       │
                    │  kg       : Dockerfile /api/graph/…  │
                    └──────────────┬──────────────────────┘
                                   │
                    voice-agent ──HTTP──► scraper REST/internal
                    kg importer ──SQL──► scraper Postgres (projection)
                    n8n ────────HTTP──► scraper /internal/* (unchanged)
```

## Data / contract boundaries (unchanged by CI)

| From | To | Contract |
|------|-----|----------|
| voice-n8n-agent | bc-tender-scraper | REST tools + executive brief HTTP |
| tenderscope-kg | bc-tender-scraper DB | Importer projection (`company_uid` in graph) |
| n8n | bc-tender-scraper | `X-Internal-Key` internal routes |
| Morning Brief | voice-n8n-agent | executive brief / narrator path |

CI validates each repo **in isolation**. It does not run cross-service e2e against production.
