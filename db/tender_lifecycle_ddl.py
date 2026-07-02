"""Runtime DDL fragments for tender lifecycle columns (P2-01)."""

from __future__ import annotations

TENDER_LIFECYCLE_COLUMN_DEFS: tuple[str, ...] = (
    "lifecycle_status VARCHAR(30) NOT NULL DEFAULT 'active'",
    "is_open BOOLEAN NOT NULL DEFAULT true",
    "lifecycle_status_override VARCHAR(30)",
    "lifecycle_override_reason TEXT NOT NULL DEFAULT ''",
    "lifecycle_override_by VARCHAR(100) NOT NULL DEFAULT ''",
    "closing_at TIMESTAMPTZ",
    "closed_at TIMESTAMPTZ",
    "awarded_at TIMESTAMPTZ",
    "cancelled_at TIMESTAMPTZ",
    "archived_at TIMESTAMPTZ",
    "missing_from_source_count INTEGER NOT NULL DEFAULT 0",
    "source_status_raw TEXT NOT NULL DEFAULT ''",
    "source_status_normalized VARCHAR(50) NOT NULL DEFAULT ''",
    "award_id INTEGER",
    "award_match_confidence DOUBLE PRECISION",
    "addenda_count INTEGER NOT NULL DEFAULT 0",
    "last_addendum_at TIMESTAMPTZ",
)

TENDER_LIFECYCLE_TABLES: tuple[str, ...] = (
    "tenders",
    "commercial_tenders",
    "arch_tenders",
)

LIFECYCLE_STATUS_CHECK_SQL = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_{table}_lifecycle_status') THEN
        ALTER TABLE {table} ADD CONSTRAINT ck_{table}_lifecycle_status
            CHECK (lifecycle_status IN (
                'new', 'active', 'closing_soon', 'closed', 'awarded',
                'cancelled', 'outcome_unknown', 'archived', 'delisted'
            ));
    END IF;
END $$;
"""


def lifecycle_add_column_statements(table: str) -> list[str]:
    return [
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {definition}"
        for definition in TENDER_LIFECYCLE_COLUMN_DEFS
    ]


def lifecycle_backfill_statement(table: str) -> str:
    return f"""
        UPDATE {table}
        SET lifecycle_status = COALESCE(lifecycle_status, 'active'),
            is_open = COALESCE(is_open, true),
            missing_from_source_count = COALESCE(missing_from_source_count, 0),
            addenda_count = COALESCE(addenda_count, 0)
        WHERE lifecycle_status IS NULL
           OR is_open IS NULL
           OR missing_from_source_count IS NULL
           OR addenda_count IS NULL
    """


def lifecycle_index_statements(table: str) -> tuple[str, ...]:
    return (
        f"CREATE INDEX IF NOT EXISTS ix_{table}_lifecycle_status ON {table} (lifecycle_status)",
        f"CREATE INDEX IF NOT EXISTS ix_{table}_is_open ON {table} (is_open) WHERE is_open = true",
        f"CREATE INDEX IF NOT EXISTS ix_{table}_closing_at ON {table} (closing_at) WHERE closing_at IS NOT NULL",
    )
