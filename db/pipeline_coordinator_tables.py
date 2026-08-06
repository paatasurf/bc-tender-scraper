"""SQLAlchemy Core table definitions for the pipeline coordinator schema
(migration 032).

Deliberately NOT declared as db.models ORM classes and NOT part of
db.models.Base.metadata. db.connection.init_db() / _run_migrations() calls
Base.metadata.create_all(bind=engine) unconditionally on every app
startup/deploy -- if these tables were mapped on Base, that single line
would silently auto-create this schema everywhere init_db() ever runs,
which is exactly the auto-apply this migration must not have. Applying
this schema is the sole responsibility of
scripts/run_pipeline_coordinator_state_migration.py --apply. This mirrors
the same precedent migration 031 set: "db.models.Permit deliberately does
not declare [the new] column yet" (db/permit_official_source_id_migration.py).

pipeline/run_coordinator.py imports these Table objects directly (Core
style: select()/insert()/update() executed through db.connection.get_session())
instead of ORM-mapped classes.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)

# A private MetaData, not db.models.Base.metadata -- see module docstring.
pipeline_coordinator_metadata = MetaData()

pipeline_coordinator_runs = Table(
    "pipeline_coordinator_runs",
    pipeline_coordinator_metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String(36), nullable=False, unique=True),
    Column("pipeline_scope", String(50), nullable=False, default="tender_data"),
    Column("status", String(20), nullable=False, default="active"),
    Column("phase", String(30), nullable=False, default="idle"),
    Column("tender_scrape_started_at", DateTime(timezone=True), nullable=True),
    Column("tender_scrape_finished_at", DateTime(timezone=True), nullable=True),
    Column("scrape_phase_started_at", DateTime(timezone=True), nullable=True),
    Column("scrape_phase_finished_at", DateTime(timezone=True), nullable=True),
    Column("import_started_at", DateTime(timezone=True), nullable=True),
    Column("import_finished_at", DateTime(timezone=True), nullable=True),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    Column("success", Boolean, nullable=True),
    Column("error", Text, nullable=False, default=""),
    Column("stale_reclaimed", Boolean, nullable=False, default=False),
    Column("lease_expires_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True)),
)

pipeline_coordinator_steps = Table(
    "pipeline_coordinator_steps",
    pipeline_coordinator_metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "run_id",
        String(36),
        ForeignKey("pipeline_coordinator_runs.run_id"),
        nullable=False,
    ),
    Column("step", String(100), nullable=False),
    Column("completed_at", DateTime(timezone=True)),
)
