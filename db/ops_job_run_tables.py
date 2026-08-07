"""SQLAlchemy Core table definitions for the ops job run schema
(migration 033, M3B).

Deliberately NOT declared as db.models ORM classes and NOT part of
db.models.Base.metadata. db.connection.init_db() / _run_migrations() calls
Base.metadata.create_all(bind=engine) unconditionally on every app
startup/deploy -- if these tables were mapped on Base, that single line
would silently auto-create this schema everywhere init_db() ever runs,
which is exactly the auto-apply this migration must not have. Applying
this schema is the sole responsibility of
scripts/run_ops_job_run_migration.py --apply. This mirrors the same
precedent migration 032 set for pipeline_coordinator_runs/steps
(db/pipeline_coordinator_tables.py).

pipeline/job_run.py imports these Table objects directly (Core style:
select()/insert()/update() executed through db.connection.get_session())
instead of ORM-mapped classes.

CheckConstraints below describe the same ck_ops_job_runs_status /
ck_ops_job_runs_trigger / ck_ops_job_run_events_event_type constraints
db/migrations/033_ops_job_runs.sql actually creates, for accurate
introspection -- DDL execution itself always comes from that SQL file
(via scripts/run_ops_job_run_migration.py), never from
Table.create()/metadata.create_all() on these objects.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
)
from sqlalchemy.dialects.postgresql import JSONB

# A private MetaData, not db.models.Base.metadata -- see module docstring.
ops_job_run_metadata = MetaData()

ops_job_runs = Table(
    "ops_job_runs",
    ops_job_run_metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String(36), nullable=False, unique=True),
    Column("job_type", String(50), nullable=False),
    Column("source", String(50), nullable=True),
    Column("trigger", String(20), nullable=False),
    Column("status", String(20), nullable=False, default="running"),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("heartbeat_at", DateTime(timezone=True), nullable=True),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    Column("lease_expires_at", DateTime(timezone=True), nullable=False),
    Column("counts", JSONB, nullable=False, default=dict),
    Column("error_present", Boolean, nullable=False, default=False),
    Column("error_summary", String(20), nullable=True),
    Column("idempotency_key", String(128), nullable=True),
    Column("created_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True)),
    CheckConstraint(
        "trigger IN ('scheduler', 'manual', 'n8n')",
        name="ck_ops_job_runs_trigger",
    ),
    CheckConstraint(
        "status IN ('running', 'success', 'failed', 'partial_failure')",
        name="ck_ops_job_runs_status",
    ),
)

ops_job_run_events = Table(
    "ops_job_run_events",
    ops_job_run_metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "run_id",
        String(36),
        ForeignKey("ops_job_runs.run_id"),
        nullable=False,
    ),
    Column("event_type", String(20), nullable=False),
    Column("step", String(100), nullable=True),
    Column("counts_delta", JSONB, nullable=True),
    Column("occurred_at", DateTime(timezone=True)),
    CheckConstraint(
        "event_type IN ('started', 'step_started', 'step_completed', 'step_failed', 'finished')",
        name="ck_ops_job_run_events_event_type",
    ),
)
