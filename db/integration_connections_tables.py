"""SQLAlchemy Core table definition for integration_connections (migration
031, AI-Workforce PR1).

Deliberately NOT a db.models ORM class and NOT part of db.models.Base.metadata.
This is a correction made during review: an earlier version of this PR
mapped IntegrationConnection as a Base subclass, and empirical testing
against a disposable local Postgres instance proved that
db.connection.init_db()'s unconditional `Base.metadata.create_all(bind=engine)`
call DOES auto-create any Base subclass, including this one -- i.e. the
table would have been silently created on every environment's next
init_db() run (which happens on every app startup), regardless of
whether anyone ever ran an --apply script. That directly contradicted
this PR's own "stays inert until explicitly applied" claim.

This module mirrors the established fix for exactly this problem already
used elsewhere for a staged-migration table: a private MetaData(), Core
Table objects, queried via db.connection.get_session() + select()/
insert()/update() (Core style), never via an ORM-mapped class. Table
creation is the sole responsibility of
db/migrations/031_integration_connections.sql, applied by an explicit
operator script -- which does not exist yet in this PR (see PR
description: not wired to automatic execution, not yet applied anywhere,
not merged).
"""

from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB

# A private MetaData, not db.models.Base.metadata -- see module docstring.
integration_connections_metadata = MetaData()

integration_connections = Table(
    "integration_connections",
    integration_connections_metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, ForeignKey("companies.id"), nullable=False),
    Column("provider", String(50), nullable=False),
    Column("nango_environment", String(20), nullable=False),
    Column("nango_provider_config_key", String(100), nullable=False),
    # Opaque string -- never parsed, formatted, or assumed to be a UUID.
    Column("nango_connection_id", Text, nullable=False),
    # The TARGET system's own org/workspace/site identity (e.g. a Jira
    # Cloud site ID, an Asana workspace gid) -- distinct from Nango's own
    # connection id, and distinct from our company_id.
    Column("external_org_id", String(200), nullable=False),
    # Explicit allow-list of projects/boards this connection may act on.
    # Semantics, fixed by this PR (architecture-review correction):
    # allowed_projects = [] means NO project is authorized -- this is a
    # deny-all default, never "unrestricted"/"all projects allowed".
    # Future application code (the executor PR) must treat an empty list
    # as "nothing to act on yet", never short-circuit it with a falsy
    # check that reads empty-list the same as "no restriction". Each
    # element's documented shape: {"project_key": str, "resource_type": str}
    # (resource_type e.g. "project" | "board" | "workspace" -- the kind of
    # target object this entry authorizes, since different providers call
    # their containers different things). Enforced at the DB level only
    # to the extent that the column must hold a JSON array (see migration
    # 031's ck_integration_connections_allowed_projects_is_array) --
    # per-element shape validation is an application-layer concern for
    # the executor PR, not this one.
    Column("allowed_projects", JSONB, nullable=False, server_default="[]"),
    Column("status", String(20), nullable=False, server_default="active"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
)
