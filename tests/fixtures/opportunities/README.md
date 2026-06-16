# Opportunities discovery baseline fixtures

Captured **before** session-scoping refactor for parity regression tests.

| File | Endpoint | Params |
|------|----------|--------|
| `baseline-construction-1921.json` | `/api/companies/1921/opportunities` | `min_score=50&limit=15` |
| `baseline-arch-19.json` | `/api/arch-companies/19/opportunities` | `min_score=40&limit=15` |

Capture:

```powershell
curl -o baseline-construction-1921.json `
  "https://bc-tender-scraper-production.up.railway.app/api/companies/1921/opportunities?min_score=50&limit=15"

curl -o baseline-arch-19.json `
  "https://bc-tender-scraper-production.up.railway.app/api/arch-companies/19/opportunities?min_score=40&limit=15"
```

Parity tests compare `(type, id, score)` tuples and list order only.
