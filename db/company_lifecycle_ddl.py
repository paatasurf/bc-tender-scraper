"""Runtime DDL fragments for company lifecycle columns."""

from __future__ import annotations

COMPANY_LIFECYCLE_TABLE = "companies"

COMPANY_LIFECYCLE_COLUMN_DEFS: tuple[str, ...] = (
    "lifecycle_status VARCHAR(30) NOT NULL DEFAULT 'active'",
    "lifecycle_status_override VARCHAR(30)",
    "last_activity_at TIMESTAMPTZ",
    "status_changed_at TIMESTAMPTZ",
    "is_operating BOOLEAN NOT NULL DEFAULT true",
)

COMPANY_LIFECYCLE_STATUS_CHECK_SQL = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_companies_lifecycle_status') THEN
        ALTER TABLE companies ADD CONSTRAINT ck_companies_lifecycle_status
            CHECK (lifecycle_status IN (
                'active', 'quiet', 'dormant', 'no_observable_activity'
            ));
    END IF;
END $$;
"""


def company_lifecycle_add_column_statements() -> list[str]:
    return [
        f"ALTER TABLE {COMPANY_LIFECYCLE_TABLE} ADD COLUMN IF NOT EXISTS {definition}"
        for definition in COMPANY_LIFECYCLE_COLUMN_DEFS
    ]


def company_lifecycle_backfill_statement() -> str:
    return f"""
        UPDATE {COMPANY_LIFECYCLE_TABLE}
        SET lifecycle_status = COALESCE(lifecycle_status, 'active'),
            is_operating = COALESCE(is_operating, true)
        WHERE lifecycle_status IS NULL
           OR is_operating IS NULL
    """


def company_lifecycle_index_statements() -> tuple[str, ...]:
    return (
        f"CREATE INDEX IF NOT EXISTS ix_{COMPANY_LIFECYCLE_TABLE}_lifecycle_status "
        f"ON {COMPANY_LIFECYCLE_TABLE} (lifecycle_status)",
        f"CREATE INDEX IF NOT EXISTS ix_{COMPANY_LIFECYCLE_TABLE}_is_operating "
        f"ON {COMPANY_LIFECYCLE_TABLE} (is_operating) WHERE is_operating = true",
    )
