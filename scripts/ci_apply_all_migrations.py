"""Apply every migration this codebase knows about, against DATABASE_URL.

CI quality infrastructure only. Two distinct migration mechanisms exist
here:

1. db.connection.init_db() -- the automatic chain (Base.metadata.create_all
   plus ~25 idempotent `_ensure_*` column/table upgrades) that production
   and every dev environment already run on every startup.
2. Five separate "Class D" migrations (db/*_migration.py, each with its own
   scripts/run_*_migration.py CLI), deliberately NOT part of init_db()'s
   automatic chain -- they carry production-safety machinery (dry-run
   artifacts, confirmation phrases, --allow-production gates) because
   applying them against a live production table is a careful, one-time,
   human-supervised operation.

Auto-applying all five here is safe ONLY because this script is invoked
exclusively against a fresh, disposable, single-use CI database (see the
quality-gate.yml steps that call this) -- never against DATABASE_URL_
PRODUCTION, and this script does not touch that variable at all. Do not
reuse this script outside CI, and do not use it as a template for a
production migration runbook: for that, use each migration's own
scripts/run_*_migration.py, which is what carries the actual safety gates.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.classification_claims_migration import apply_classification_claims_migration
from db.connection import get_engine, init_db
from db.ops_job_run_migration import apply_ops_job_run_migration
from db.permit_official_source_id_migration import (
    apply_permit_official_source_id_migration,
)
from db.pipeline_coordinator_migration import apply_pipeline_coordinator_migration
from db.track_record_migration import apply_company_track_record_migration


def main() -> None:
    ok = init_db(raise_on_failure=True)
    if not ok:
        raise SystemExit(
            "init_db() reported degraded on a fresh CI database -- unexpected"
        )
    print("init_db: ok")

    engine = get_engine()
    class_d_migrations = (
        ("031_permit_official_source_id", apply_permit_official_source_id_migration),
        ("030_company_track_record", apply_company_track_record_migration),
        ("029_classification_claims", apply_classification_claims_migration),
        ("033_ops_job_runs", apply_ops_job_run_migration),
        ("032_pipeline_coordinator_state", apply_pipeline_coordinator_migration),
    )
    for name, apply_fn in class_d_migrations:
        result = apply_fn(engine)
        print(f"{name}: ok ({result})")


if __name__ == "__main__":
    main()
