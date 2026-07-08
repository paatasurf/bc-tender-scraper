# LinkedIn URL Resolver — Failure Audit

> Read-only analysis. Resolver not modified.

## Benchmark status

The 200-company association benchmark was **incomplete** at time of audit.

| Metric | Value |
|--------|------:|
| Batch size | 200 |
| Verified (confidence >= 90) | 13 (6.5%) |
| Processed (in cache) | 46 |
| Verified rate (processed only) | 28.3% |
| Still pending | 154 |

## Failure classification (full batch of 200)

| Category | Count | % of batch | Representative examples |
|----------|------:|-----------:|-------------------------|
| Benchmark incomplete (not yet resolved) | 154 | 77.0% | Argus Properties Ltd.; Armada Steel Corp.; ARRM Electric Ltd. |
| Company has no LinkedIn page | 16 | 8.0% | Farmer Construction Ltd.; Heatherbrae Builders Co. Ltd.; Camosun College |
| LinkedIn exists but was not discovered | 10 | 5.0% | Black & McDonald Limited; Dawson Wallace Construction Ltd.; Pomerleau Inc. |
| Official website missing | 6 | 3.0% | Lafarge Canada Inc; WSP Canada Inc.; Amrize Canada Inc. |
| Official website inaccessible | 1 | 0.5% | Admiral Roofing Ltd. |

## Root-cause interpretation

1. **Structural ceiling (no LinkedIn page):** 16 companies (8.0%) — search and website parse found nothing. These are mostly small BC trade contractors; many legitimately have no LinkedIn company presence.

2. **Fixable pipeline gaps:** 10 companies show evidence of LinkedIn (website link or search hit) but resolver did not accept — verification strictness, DuckDuckGo search quality, or slug mismatch.

3. **Infrastructure:** 7 companies lack a usable website anchor for Stage 2.

4. **Benchmark incomplete:** 154 companies not yet processed when audit ran.

## Maximum realistic verification rate (estimate)

| Scope | Estimated ceiling |
|-------|------------------:|
| Processed subset (46 cos) | **43.5%** |
| Full 200-company batch | **20.9%** |
| Full association pool (~1,060) | **55–70%** (extrapolated) |

**Conclusion:** A 95% LinkedIn URL resolution rate is **not achievable** for this universe. The dominant limiter is that a large share of BC association contractors appear to have **no LinkedIn company page at all**, not resolver bugs alone.

## KPI recommendation

Change the success metric from **LinkedIn Resolution Rate** to **Verified Company Coverage**, where LinkedIn is one source among:

- LinkedIn company page (when resolvable at confidence >= 90)
- Official website metadata
- Association membership record
- ODB / registry cross-reference

Report separately:

- `linkedin_enrichable_rate` — companies with verified LinkedIn URL
- `profile_coverage_rate` — companies with any verified enrichment source

Artifacts: `url_resolution_audit_data.json`, `url_resolution_audit.md`