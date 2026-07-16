# Stage 2A Evidence Link Audit — Acceptance Contract

## Purpose

This contract defines how to interpret the read-only output of
`scripts/run_evidence_link_readiness_audit.py`. It prevents a clean-looking
sample or a high raw link rate from being presented as proof of identity or
market-coverage quality.

## Structural integrity gate

For both `permits` and `contract_awards`, all of the following must equal zero:

- `orphan_count`
- `broken_redirect_count`
- `cycle_count`
- `depth_exhausted_count`
- `excluded_target_count`

Any non-zero value is a **FAIL**. The audit report is diagnostic only; a failure
does not authorize an automatic repair.

## Canonical-target readiness

`non_canonical_count` is a **WARN** when greater than zero. A resolvable alias
redirect is not structural corruption, but direct evidence links should
eventually target canonical company passports. The publication-cutover target
is zero direct non-canonical links, reached through a separately approved,
audited migration or write workflow.

## Coverage interpretation

For each evidence source, report the raw linkage rate as:

`rows_with_company_id / total_rows`

Also report the unlinked count (`rows_without_company_id`). This is an inventory
baseline, not a precision or recall score. The Stage 2A report does not define
which rows contain enough attributable company evidence to be link-eligible;
therefore it cannot by itself prove a 95% linkage-quality target.

A customer-facing accuracy statement requires a separately sampled and manually
adjudicated benchmark with an explicit eligible denominator.

## Tender linkage gate

`tenders.schema_gap = true` is expected for the current Stage 2A implementation,
but it is a **BLOCKER** for complete permit/tender/award Evidence Link coverage.
Stage 2A reports this gap and does not authorize adding the column or populating
links.

## Reproducibility

- `dataset_hash` must be a non-empty 64-character SHA-256 value for each audited
  evidence source.
- Re-running against an unchanged transactionally stable dataset must reproduce
  the same `dataset_hash`.
- `report_hash` is a bounded summary/sample fingerprint and must not be used as
  the authoritative full-dataset integrity proof.
- The saved artifact must record the execution timestamp, deployed commit SHA,
  target environment (masked), and audit script version/commit.

## Required result summary

The operator summary must include:

1. total, linked, unlinked, and raw linkage rate for permits and awards;
2. every structural-integrity count;
3. non-canonical count and representative samples;
4. both authoritative dataset hashes;
5. tender row count and schema-gap state;
6. an explicit statement that the run was Class A/read-only;
7. separate recommendations, with no repairs performed implicitly.
