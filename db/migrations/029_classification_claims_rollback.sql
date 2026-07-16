-- Rollback for migration 029 (classification_claims schema foundation).
--
-- DO NOT RUN THIS FILE DIRECTLY. It contains no emptiness guard of its own —
-- the guard lives in Python, in
-- db.classification_claims_migration.apply_classification_claims_rollback(),
-- which refuses to execute any of these statements if ANY of the six tables
-- below contains at least one row. Apply only via:
--
--   python scripts/run_classification_claims_migration.py --rollback --allow-production
--
-- This migration never backfills, seeds, or writes data — a non-empty table
-- at rollback time means something outside this migration's scope wrote to
-- it (e.g. PR-B2's Gateway, once merged and wired up), and rollback must not
-- silently discard that data.
--
-- Drop order is the exact reverse of the CREATE order in
-- 029_classification_claims.sql.

DROP TABLE IF EXISTS resolved_company_beliefs;
DROP TABLE IF EXISTS projector_runs;
DROP TABLE IF EXISTS claim_events;
DROP TABLE IF EXISTS claim_evidence;
DROP TABLE IF EXISTS classification_claims;
DROP TABLE IF EXISTS rule_set_versions;
