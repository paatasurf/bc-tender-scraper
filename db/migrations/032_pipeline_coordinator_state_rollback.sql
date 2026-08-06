-- Rollback for migration 032: Persistent pipeline coordinator state (R1).
--
-- Drops both new tables and their indexes. Additive-only migration, so
-- rollback is a clean drop -- no existing table or column is restored
-- because none was ever touched. Never run against a database where
-- pipeline/run_coordinator.py has already been switched to read/write
-- these tables (that would remove the coordinator's only backing store).

DROP TABLE IF EXISTS pipeline_coordinator_steps;
DROP TABLE IF EXISTS pipeline_coordinator_runs;
