"""FK remap helpers for canonical company merge."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from db.models import (
    ClientProfile,
    CompanyWiki,
    ContractAward,
    GoogleEnrichmentLog,
    Permit,
    TenderOutcome,
)


@dataclass(frozen=True)
class FkRemapSpec:
    table: str
    column: str
    model: type | None = None
    unique_columns: tuple[str, ...] = ()


FK_REMAP_SPECS: tuple[FkRemapSpec, ...] = (
    FkRemapSpec("contract_awards", "company_id", ContractAward),
    FkRemapSpec("tender_outcomes", "company_id", TenderOutcome, ("company_id", "tender_id")),
    FkRemapSpec("client_profiles", "company_id", ClientProfile),
    FkRemapSpec("company_wiki", "company_id", CompanyWiki, ("company_id", "company_kind")),
    FkRemapSpec("google_enrichment_logs", "company_id", GoogleEnrichmentLog),
    FkRemapSpec("permits", "company_id", Permit),
)


def build_alias_to_canonical_map(
    alias_ids: dict[int, int],
) -> dict[int, int]:
    """alias company_id -> canonical company_id."""
    return dict(alias_ids)


def remap_company_foreign_keys(
    session: Session,
    alias_to_canonical: dict[int, int],
    *,
    run_id: int | None = None,
    rollback_store: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Remap FK columns from alias company ids to canonical ids."""
    if not alias_to_canonical:
        return {"tables": {}, "updated": 0, "skipped_conflicts": 0}

    alias_ids = sorted(alias_to_canonical.keys())
    summary: dict[str, Any] = {"tables": {}, "updated": 0, "skipped_conflicts": 0}

    for spec in FK_REMAP_SPECS:
        table_summary = {"updated": 0, "skipped_conflicts": 0, "rows": []}
        print(f"[FkRemap] {spec.table}.{spec.column}...")
        if spec.unique_columns:
            _remap_with_conflict_checks(
                session,
                spec,
                alias_to_canonical,
                alias_ids,
                table_summary,
                summary,
                rollback_store,
            )
        else:
            _remap_batch(
                session,
                spec,
                alias_to_canonical,
                alias_ids,
                table_summary,
                summary,
                rollback_store,
            )
        summary["tables"][spec.table] = table_summary
        print(f"[FkRemap] {spec.table}: updated={table_summary['updated']} skipped={table_summary['skipped_conflicts']}")

    return summary


def _remap_batch(
    session: Session,
    spec: FkRemapSpec,
    alias_to_canonical: dict[int, int],
    alias_ids: list[int],
    table_summary: dict[str, Any],
    summary: dict[str, Any],
    rollback_store: list[dict[str, Any]] | None,
) -> None:
    for alias_id in alias_ids:
        canonical_id = alias_to_canonical[alias_id]
        if alias_id == canonical_id:
            continue

        row_ids = session.execute(
            text(f"SELECT id FROM {spec.table} WHERE {spec.column} = :alias_id"),
            {"alias_id": alias_id},
        ).scalars().all()

        if rollback_store is not None:
            for row_id in row_ids:
                rollback_store.append(
                    {
                        "entity_type": f"fk:{spec.table}",
                        "entity_id": int(row_id),
                        "before_json": {spec.column: alias_id},
                    }
                )

        if not row_ids:
            continue

        result = session.execute(
            text(
                f"UPDATE {spec.table} SET {spec.column} = :canonical_id "
                f"WHERE {spec.column} = :alias_id"
            ),
            {"canonical_id": canonical_id, "alias_id": alias_id},
        )
        updated = int(result.rowcount or 0)
        table_summary["updated"] += updated
        summary["updated"] += updated


def _remap_with_conflict_checks(
    session: Session,
    spec: FkRemapSpec,
    alias_to_canonical: dict[int, int],
    alias_ids: list[int],
    table_summary: dict[str, Any],
    summary: dict[str, Any],
    rollback_store: list[dict[str, Any]] | None,
) -> None:
    for alias_id in alias_ids:
        canonical_id = alias_to_canonical[alias_id]
        if alias_id == canonical_id:
            continue

        rows = session.execute(
            text(f"SELECT * FROM {spec.table} WHERE {spec.column} = :alias_id"),
            {"alias_id": alias_id},
        ).mappings().all()

        for row in rows:
            row_dict = dict(row)
            row_id = row_dict.get("id")
            if rollback_store is not None and row_id is not None:
                rollback_store.append(
                    {
                        "entity_type": f"fk:{spec.table}",
                        "entity_id": int(row_id),
                        "before_json": {spec.column: alias_id},
                    }
                )

            conflict = _has_unique_conflict(
                session,
                spec.table,
                spec.unique_columns,
                row_dict,
                spec.column,
                alias_id,
                canonical_id,
            )
            if conflict:
                table_summary["skipped_conflicts"] += 1
                summary["skipped_conflicts"] += 1
                continue

            session.execute(
                text(
                    f"UPDATE {spec.table} SET {spec.column} = :canonical_id "
                    f"WHERE id = :row_id"
                ),
                {"canonical_id": canonical_id, "row_id": row_id},
            )
            table_summary["updated"] += 1
            summary["updated"] += 1


def _has_unique_conflict(
    session: Session,
    table: str,
    unique_columns: tuple[str, ...],
    row: dict[str, Any],
    fk_column: str,
    alias_id: int,
    canonical_id: int,
) -> bool:
    current_id = int(row.get("id") or 0)
    filters: list[str] = []
    params: dict[str, Any] = {"current_id": current_id}
    for idx, col in enumerate(unique_columns):
        param = f"c_{idx}"
        if col == fk_column:
            params[param] = canonical_id
        else:
            params[param] = row.get(col)
        filters.append(f"{col} = :{param}")

    sql = (
        f"SELECT id FROM {table} WHERE {' AND '.join(filters)} "
        f"AND id <> :current_id LIMIT 1"
    )
    return session.execute(text(sql), params).first() is not None
