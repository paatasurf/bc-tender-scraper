"""SQLAlchemy Core table definitions for the company on-demand enrichment
schema (migration 034, RFC Phase 1: docs/COMPANY_ON_DEMAND_ENRICHMENT_RFC.md S5).

Deliberately NOT declared as db.models ORM classes and NOT part of
db.models.Base.metadata. db.connection.init_db() / _run_migrations() calls
Base.metadata.create_all(bind=engine) unconditionally on every app
startup/deploy -- if these tables were mapped on Base, that single line
would silently auto-create this schema everywhere init_db() ever runs,
which is exactly the auto-apply this migration must not have. Applying
this schema is the sole responsibility of
scripts/run_company_enrichment_migration.py --apply. This mirrors the same
precedent migrations 032/033 set (db/pipeline_coordinator_tables.py,
db/ops_job_run_tables.py).

pipeline/company_enrichment/orchestrator.py imports these Table objects
directly (Core style: select()/insert()/update() executed through
db.connection.get_session()) instead of ORM-mapped classes.

CheckConstraints below describe the same
ck_company_enrichment_jobs_trigger / ck_company_enrichment_jobs_status
constraints db/migrations/034_company_enrichment.sql actually creates, for
accurate introspection -- DDL execution itself always comes from that SQL
file (via scripts/run_company_enrichment_migration.py), never from
Table.create()/metadata.create_all() on these objects.
"""

from __future__ import annotations

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)

# A private MetaData, not db.models.Base.metadata -- see module docstring.
company_enrichment_metadata = MetaData()

company_enrichment_fields = Table(
    "company_enrichment_fields",
    company_enrichment_metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, ForeignKey("companies.id"), nullable=False),
    Column("field_name", String(50), nullable=False),
    Column("value", Text, nullable=False),
    Column("source", String(30), nullable=False),
    Column("confidence", Float, nullable=True),
    Column("verified", Boolean, nullable=False, default=False),
    Column("fetched_at", DateTime(timezone=True), nullable=False),
    Column("superseded_at", DateTime(timezone=True), nullable=True),
    Column("run_id", String(36), nullable=True),
)

company_enrichment_jobs = Table(
    "company_enrichment_jobs",
    company_enrichment_metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String(36), nullable=False, unique=True),
    Column("company_id", Integer, ForeignKey("companies.id"), nullable=False),
    Column(
        "trigger",
        String(20),
        CheckConstraint(
            "trigger IN ('profile_view', 'agent', 'manual')",
            name="ck_company_enrichment_jobs_trigger",
        ),
        nullable=False,
    ),
    Column(
        "status",
        String(20),
        CheckConstraint(
            "status IN ('running', 'success', 'failed', 'partial_success')",
            name="ck_company_enrichment_jobs_status",
        ),
        nullable=False,
        default="running",
    ),
    Column("providers_attempted", ARRAY(String(30)), nullable=False, default=list),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    Column("lease_expires_at", DateTime(timezone=True), nullable=False),
)
