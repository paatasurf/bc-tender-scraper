-- Rollback for migration 033: Ops job run / job run event schema
-- foundation (M3B).
--
-- Drops both new tables and their indexes. Additive-only migration, so
-- rollback is a clean drop -- no existing table or column is restored
-- because none was ever touched. Never run against a database where
-- pipeline/job_run.py has already been wired into any real caller (M3C+)
-- -- that would remove the only backing store for that job history.

DROP TABLE IF EXISTS ops_job_run_events;
DROP TABLE IF EXISTS ops_job_runs;
