"""FK-only company lifecycle distribution probe (read-only, no init_db)."""
from __future__ import annotations


from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from db.connection import get_engine
from db.db_safety import guard_readonly_db
_SCRIPT = Path(__file__).name

FK_ONLY_CLASSIFICATION_SQL = text(
    """
    WITH award_activity AS (
        SELECT company_id, MAX(SUBSTRING(award_date FROM 1 FOR 10)::date) AS last_d
        FROM contract_awards
        WHERE company_id IS NOT NULL
          AND award_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
        GROUP BY company_id
    ),
    outcome_activity AS (
        SELECT company_id, MAX(recorded_at::date) AS last_d
        FROM tender_outcomes
        GROUP BY company_id
    ),
    combined AS (
        SELECT company_id, MAX(last_d) AS last_d
        FROM (
            SELECT company_id, last_d FROM award_activity
            UNION ALL
            SELECT company_id, last_d FROM outcome_activity
        ) x
        GROUP BY company_id
    ),
    classified AS (
        SELECT
            c.id,
            CASE
                WHEN cb.last_d IS NULL THEN 'no_observable_activity'
                WHEN (DATE '2026-07-02' - cb.last_d) <= 365 THEN 'active'
                WHEN (DATE '2026-07-02' - cb.last_d) <= 730 THEN 'quiet'
                ELSE 'dormant'
            END AS status
        FROM companies c
        LEFT JOIN combined cb ON cb.company_id = c.id
    )
    SELECT status, COUNT(*) AS n
    FROM classified
    GROUP BY status
    ORDER BY n DESC
    """
)


def main() -> None:
    guard_readonly_db(_SCRIPT)
    engine = get_engine()
    with engine.connect() as conn:
        total = conn.execute(text("SELECT COUNT(*) FROM companies")).scalar() or 0
        award_linked = conn.execute(
            text(
                """
                SELECT COUNT(DISTINCT company_id)
                FROM contract_awards
                WHERE company_id IS NOT NULL
                  AND award_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
                """
            )
        ).scalar()
        outcome_linked = conn.execute(
            text("SELECT COUNT(DISTINCT company_id) FROM tender_outcomes")
        ).scalar()
        any_fk = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM (
                    SELECT company_id FROM contract_awards
                    WHERE company_id IS NOT NULL
                      AND award_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
                    UNION
                    SELECT company_id FROM tender_outcomes
                ) s
                """
            )
        ).scalar()

        print("FK-only expected post-resolve (ref 2026-07-02):")
        print(f"  total companies: {total:,}")
        print(f"  distinct award FK companies: {award_linked:,}")
        print(f"  distinct tender_outcomes companies: {outcome_linked:,}")
        print(f"  companies with any FK activity: {any_fk:,}")
        for row in conn.execute(FK_ONLY_CLASSIFICATION_SQL).all():
            pct = row.n / total * 100 if total else 0
            print(f"  {row.status}: {row.n:,} ({pct:.1f}%)")


if __name__ == "__main__":
    main()
