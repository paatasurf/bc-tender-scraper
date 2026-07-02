"""Runtime DDL fragments for permit lifecycle columns."""

from __future__ import annotations

PERMIT_LIFECYCLE_COLUMN_DEFS: tuple[str, ...] = (
    "lifecycle_status VARCHAR(20) NOT NULL DEFAULT 'active'",
    "lifecycle_status_override VARCHAR(20)",
    "status_changed_at TIMESTAMPTZ",
    "is_active BOOLEAN NOT NULL DEFAULT true",
    "source_status_raw VARCHAR(100) NOT NULL DEFAULT ''",
)

PERMIT_LIFECYCLE_TABLE = "permits"

PERMIT_LIFECYCLE_STATUS_CHECK_SQL = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_permits_lifecycle_status') THEN
        ALTER TABLE permits ADD CONSTRAINT ck_permits_lifecycle_status
            CHECK (lifecycle_status IN (
                'active', 'completed', 'cancelled', 'stale', 'unknown'
            ));
    END IF;
END $$;
"""


def permit_lifecycle_add_column_statements() -> list[str]:
    return [
        f"ALTER TABLE {PERMIT_LIFECYCLE_TABLE} ADD COLUMN IF NOT EXISTS {definition}"
        for definition in PERMIT_LIFECYCLE_COLUMN_DEFS
    ]


def permit_lifecycle_backfill_statement() -> str:
    return f"""
        UPDATE {PERMIT_LIFECYCLE_TABLE}
        SET lifecycle_status = COALESCE(lifecycle_status, 'active'),
            is_active = COALESCE(is_active, true)
        WHERE lifecycle_status IS NULL
           OR is_active IS NULL
    """


def permit_lifecycle_index_statements() -> tuple[str, ...]:
    return (
        f"CREATE INDEX IF NOT EXISTS ix_{PERMIT_LIFECYCLE_TABLE}_lifecycle_status "
        f"ON {PERMIT_LIFECYCLE_TABLE} (lifecycle_status)",
        f"CREATE INDEX IF NOT EXISTS ix_{PERMIT_LIFECYCLE_TABLE}_is_active "
        f"ON {PERMIT_LIFECYCLE_TABLE} (is_active) WHERE is_active = true",
    )
