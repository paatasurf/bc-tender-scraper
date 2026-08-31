-- Rollback for migration 034: Company on-demand enrichment schema
-- foundation. Drops both new tables and all their indexes/constraints.
-- No existing table is touched by the forward migration, so nothing else
-- needs to be reverted here.

DROP TABLE IF EXISTS company_enrichment_fields;
DROP TABLE IF EXISTS company_enrichment_jobs;
