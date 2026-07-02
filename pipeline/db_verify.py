from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.models import ArchTender, CommercialTender, Tender


class DbVerificationError(RuntimeError):
    pass


def count_table_rows(session: Session) -> dict[str, int]:
    return {
        "tenders": session.scalar(select(func.count()).select_from(Tender)) or 0,
        "commercial_tenders": session.scalar(select(func.count()).select_from(CommercialTender)) or 0,
        "arch_tenders": session.scalar(select(func.count()).select_from(ArchTender)) or 0,
    }


def verify_database_counts(
    session: Session,
    import_counts: dict[str, int],
    *,
    previous_counts: dict[str, int] | None = None,
) -> dict[str, int | str]:
    """Verify DB row counts after import; import batch must be > 0 for tender tables."""
    db_counts = count_table_rows(session)
    errors: list[str] = []

    for key in ("tenders", "commercial_tenders", "arch_tenders"):
        imported = import_counts.get(key, 0)
        total = db_counts.get(key, 0)
        if imported <= 0:
            errors.append(f"{key}: import batch reported {imported} rows")
        if total <= 0:
            errors.append(f"{key}: database total is {total}")

    if previous_counts:
        for key in ("tenders", "commercial_tenders", "arch_tenders"):
            if db_counts.get(key, 0) < previous_counts.get(key, 0):
                errors.append(
                    f"{key}: database count decreased "
                    f"({previous_counts.get(key)} -> {db_counts.get(key)})"
                )

    if errors:
        raise DbVerificationError("; ".join(errors))

    summary = {
        **db_counts,
        "import_batch_tenders": import_counts.get("tenders", 0),
        "import_batch_commercial_tenders": import_counts.get("commercial_tenders", 0),
        "import_batch_arch_tenders": import_counts.get("arch_tenders", 0),
    }
    print(f"[Pipeline] Database verification passed: {summary}")
    return summary
