# Google Business curated enrichment — research

Enriches **existing** Class A/B companies from `research/enrichment/company_profiles.json`.

**No database writes.** No new company records.

## Fields captured (when Google Verified)

- Google Place ID
- Official website (from Google)
- Business category
- Address
- Phone
- Rating
- Review count
- Google Maps URL

## Status model

| `status` | `google_verified` | Meaning |
|----------|-------------------|---------|
| `Google Verified` | `true` | Matched Google Business profile above confidence threshold |
| `Not Found` | `false` | Lookup completed, no acceptable match |
| `Failed` | `false` | Provider or lookup error |
| `null` | `false` | Lookup not run (`--dry-run`) |

## Prerequisites

Set `APIFY_TOKEN` (default provider). Optional: `GOOGLE_PROVIDER`, `APIFY_ACTOR_ID`.

## Usage

```powershell
cd C:\Users\DAVIDSURF\Projects\bc-tender-scraper

# Validation gate — 20 Class A companies
python research/google/run_curated_google_enrichment.py --classes A --limit 20

# Full Class A/B (after validation passes)
python research/google/run_curated_google_enrichment.py --classes A B
```

Reports: `curated_google_report.json`, `curated_google_report.md`, `curated_google_statistics.json`.
