# Phase 1 — Deployment with Quality Gate

## Current deploy path (unchanged)

```
developer → PR / push → GitHub master → Railway auto-deploy → healthcheck
```

Phase 1 **adds** a GitHub Actions Quality Gate on the same events. It does **not** replace Railway builders (`NIXPACKS` / `DOCKERFILE`) or start commands.

## Target safe path

```
PR opened
  → Quality Gate (GitHub Actions) must pass
  → Branch protection blocks merge if red
  → Merge to master
  → Quality Gate runs on push
  → Railway Wait-for-CI succeeds
  → Railway build + deploy
  → /api/health (or service health path)
```

## Per-service health paths (existing)

| Service | Health path |
|---------|-------------|
| bc-tender-scraper | `/api/health` |
| voice-n8n-agent | `/health` |
| tenderscope-kg | `/api/graph/health` |

## Operator checklist (one-time)

### GitHub (each repo)

1. Settings → Branches → Protect `master`
2. Require status checks: **Quality Gate**
3. Do not allow bypass for admins in production discipline mode (recommended)

### Railway (each service)

1. Open service → Settings → related GitHub repo
2. Enable **Wait for CI** / required check suites for `Quality Gate`
3. Confirm deploy branch remains `master`
4. Do **not** inject `DATABASE_URL_PRODUCTION` into GitHub Actions

### n8n

No change. Continues to call production `/internal/*` after successful Railway deploy.

## Deployment readiness artifact

Each workflow uploads `reports/deployment-readiness.md` with:

- passed / failed / error counts per suite
- execution time
- coverage line-rate (when available)
- **Ready for deploy: YES/NO**

## Manual dispatch

Actions → **Quality Gate** → Run workflow (useful before a hot-fix merge).
