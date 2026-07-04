# Script classification registry

Single source of truth for CLI script database risk classes.

## Classes

| Class | Name | Writes DB | Calls init_db | Production |
|-------|------|-----------|---------------|------------|
| **A** | No Write | No (read-only SELECT or no DB) | No | Read-only via `--use-production` when script reads DB |
| **B** | Local Write | Yes (data) | No | Local default; `--allow-production` + phrase for prod |
| **C** | Registry Write | Yes (registry data) | Sometimes | Local default; `--allow-production` + valid dry-run artifact |
| **D** | Schema DDL | Yes (schema/data) | Yes | `--allow-production` + confirmation phrase only |

**A vs B:** Class A never mutates the database (includes scripts with no Postgres
connection and read-only probes). Class B performs local data writes (backfill,
cache warm, staging loads) but not registry merge workflows.

## Runtime escalation (critical)

The **highest-risk operation actually executed at runtime** determines the
**effective** class, regardless of nominal class.

If a Class A, B, or C script calls `init_db()` or any DDL mid-run, `db_safety.py`
escalates to **Class D** at that point and re-checks authorization (including
production confirmation when applicable).

## Class C dry-run validity

`run_company_canonical_merge.py --apply` refuses when the referenced dry-run
artifact lacks matching `git_commit_sha` and `dataset_fingerprint` (counts,
max timestamps, identity checksum, schema migration version).

---

| Script | Nominal Class | Writes DB | Calls init_db | Production Allowed | Last Reviewed | Notes |
|--------|---------------|-----------|---------------|-------------------|---------------|-------|

| `_audit_person_permit_pipeline.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 | Read-only audit pipeline. |
| `_insert_test_client_profile.py` | B | Yes | No | Local write; `--allow-production` + phrase | 2026-07-03 | Upserts client_profiles test row. |
| `_list_lifecycle_routes.py` | A | No | No | N/A | 2026-07-03 |  |
| `_probe_alias_breakdown.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 | Read-only probe. |
| `_probe_capability_audit.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 | Read-only probe. |
| `_probe_company_entity_counts.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 | Read-only probe. |
| `_probe_company_lifecycle.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 | Read-only probe. |
| `_probe_company_lifecycle_any_signal.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 | Read-only probe. |
| `_probe_company_lifecycle_extra.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 | Read-only probe. |
| `_probe_company_lifecycle_fk_only.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 | Read-only probe. |
| `_probe_company_lifecycle_reconcile.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 | Read-only probe. |
| `_probe_company_lifecycle_status.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 | Read-only probe. |
| `_probe_data_inventory.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 | Read-only probe. |
| `_probe_db_counts.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 | Read-only probe. |
| `_probe_db_enterprise_seed.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 | Read-only probe. |
| `_probe_db_ping.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 | Read-only probe. |
| `_probe_ledcor_faucet.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 | Read-only probe. |
| `_probe_merge_ledcor_pontem.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 | Read-only probe. |
| `_probe_permit_lifecycle_state.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 | Read-only probe. |
| `_probe_reliability_top5.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 | Read-only probe. |
| `_probe_unified_opportunities_lifecycle.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 | Read-only probe. |
| `_quick_merge_status.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 |  |
| `_readonly_pi_check.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 | Read-only audit. |
| `_readonly_schema_audit.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 | Read-only audit. |
| `_research_surrey_agol.py` | A | No | No | N/A | 2026-07-03 | External research; no DB. |
| `_research_surrey_archives.py` | A | No | No | N/A | 2026-07-03 | External research; no DB. |
| `_research_surrey_deep.py` | A | No | No | N/A | 2026-07-03 | External research; no DB. |
| `_research_surrey_hub.py` | A | No | No | N/A | 2026-07-03 | External research; no DB. |
| `_research_surrey_permits_sources.py` | A | No | No | N/A | 2026-07-03 | External research; no DB. |
| `_retrofit_db_guards.py` | A | No | Yes | Read-only (`--use-production`) | 2026-07-03 | Codegen utility. |
| `_retrofit_readonly_guards.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 | Codegen utility. |
| `_simulate_permit_resolve.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 |  |
| `_smoke_google_enrichment_8638.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 | HTTP smoke against Railway API; no direct Postgres. |
| `_summarize_audit.py` | A | No | No | N/A | 2026-07-03 |  |
| `_verify_merge_apply_local.py` | A | No | Yes | Read-only (`--use-production`) | 2026-07-03 | Post-apply verification reads only. |
| `apply_permit_migration.py` | D | Yes | No | `--allow-production` + phrase | 2026-07-03 |  |
| `audit_awards_name_search.py` | A | No | No | N/A | 2026-07-03 |  |
| `audit_ci_awards_benchmark.py` | A | No | No | N/A | 2026-07-03 |  |
| `audit_ci_awards_overlap.py` | A | No | No | N/A | 2026-07-03 |  |
| `audit_ci_awards_table.py` | A | No | No | N/A | 2026-07-03 |  |
| `audit_db.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 |  |
| `audit_pipeline_ordering.py` | A | No | No | N/A | 2026-07-03 |  |
| `audit_production_contract_awards.py` | A | No | No | N/A | 2026-07-03 |  |
| `audit_production_tender_sources.py` | A | No | No | N/A | 2026-07-03 |  |
| `audit_tender_categories.py` | A | No | No | N/A | 2026-07-03 |  |
| `backfill_closing_at_local.py` | D | Yes | Yes | `--allow-production` + phrase | 2026-07-03 |  |
| `backfill_identity_phase2.py` | D | Yes | Yes | `--allow-production` + phrase | 2026-07-03 |  |
| `bd_quality_audit.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 |  |
| `build_enterprise_registry_seed.py` | C | Yes | Yes | Local write; `--allow-production` + dry-run | 2026-07-03 | Effective D when init_db runs; --skip-init-db keeps read path at A. |
| `capture_opportunities_baselines.py` | A | No | No | N/A | 2026-07-03 |  |
| `check_staging_load.py` | A | No | No | N/A | 2026-07-03 |  |
| `compare_enterprise_seed_runs.py` | A | No | No | N/A | 2026-07-03 |  |
| `compare_opportunity_tenders.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 |  |
| `debug_allowlist.py` | A | No | No | N/A | 2026-07-03 |  |
| `debug_award_market.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 |  |
| `demo_class_b_escalation.py` | B | Yes | Yes | Local write; `--allow-production` + phrase | 2026-07-03 | Demo: nominal B (local write), escalates to D on init_db(). |
| `demo_db_safety_guard.py` | A | No | No | N/A | 2026-07-03 | Spawns subprocess only; no direct DB. |
| `diag_geo_1921_670.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 |  |
| `enrich_early_signal_events.py` | A | No | No | N/A | 2026-07-03 |  |
| `f005_purge_non_construction_matches.py` | B | Yes | No | Local write; `--allow-production` + phrase | 2026-07-03 | Dry-run default (Class A path); --apply deletes rows (Class B write). |
| `google_enrichment_audit.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 |  |
| `google_enrichment_rating_gap_audit.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 | Read-only DB + external Apify; never writes. |
| `investigate_closing_dates.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 | Read-only DB investigation. |
| `migrate_architects_to_arch_companies.py` | B | Yes | No | Local write; `--allow-production` + phrase | 2026-07-03 | Dry-run default; --commit inserts rows. |
| `populate_project_contacts.py` | A | No | No | N/A | 2026-07-03 |  |
| `probe_claude_deps_production.py` | A | No | No | N/A | 2026-07-03 |  |
| `probe_vercel_006.py` | A | No | No | N/A | 2026-07-03 |  |
| `probe_vercel_bundles.py` | A | No | No | N/A | 2026-07-03 |  |
| `report_opportunity_before_after.py` | A | No | No | N/A | 2026-07-03 |  |
| `run_company_canonical_merge.py` | C | Yes | Yes | Local write; `--allow-production` + dry-run | 2026-07-03 | Effective escalates to Class D whenever init_db() runs. --apply requires fresh dry-run artifact. |
| `run_construction_tiers.py` | D | Yes | Yes | `--allow-production` + phrase | 2026-07-03 |  |
| `run_odbus_import.py` | D | Yes | Yes | `--allow-production` + phrase | 2026-07-03 |  |
| `run_orgbook_import.py` | D | Yes | Yes | `--allow-production` + phrase | 2026-07-03 |  |
| `run_registry_verification_match.py` | D | Yes | Yes | `--allow-production` + phrase | 2026-07-03 |  |
| `run_vancouver_permit_backfill.py` | D | Yes | Yes | `--allow-production` + phrase | 2026-07-03 |  |
| `smoke_bd_intelligence.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 |  |
| `smoke_discovery.py` | A | No | No | N/A | 2026-07-03 |  |
| `trace_awards_e2e.py` | A | No | No | N/A | 2026-07-03 |  |
| `trace_awards_mismatch.py` | A | No | No | N/A | 2026-07-03 |  |
| `validate_006_production.py` | A | No | No | N/A | 2026-07-03 | Verification script. |
| `validate_awards_fix.py` | A | No | No | N/A | 2026-07-03 | Verification script. |
| `validate_cohort_isolation_007.py` | A | No | No | N/A | 2026-07-03 | Verification script. |
| `verify_006_lmdg_fix.py` | A | No | No | N/A | 2026-07-03 | Verification script. |
| `verify_company_opportunities_deploy.py` | A | No | No | N/A | 2026-07-03 | Verification script. |
| `verify_lifecycle_opportunity_filter_local.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 | Verification script. |
| `verify_lifecycle_schema.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 | Verification script. |
| `verify_lifecycle_transitions_local.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 | Verification script. |
| `verify_local_staging.py` | A | No | No | N/A | 2026-07-03 | Verification script. |
| `verify_opportunities_concurrent.py` | A | No | No | N/A | 2026-07-03 | Verification script. |
| `verify_public_staging.py` | A | No | No | N/A | 2026-07-03 | Verification script. |
| `verify_staging.py` | A | No | No | N/A | 2026-07-03 | Verification script. |
| `verify_tender_presence.py` | A | No | No | Read-only (`--use-production`) | 2026-07-03 | Verification script. |
| `warm_tender_match_cache.py` | B | Yes | No | Local write; `--allow-production` + phrase | 2026-07-03 | Writes tender_matches cache rows. |
