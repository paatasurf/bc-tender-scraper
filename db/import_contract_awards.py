from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from db.models import ContractAward
from pipeline.company_matching import build_company_indexes, match_vendor_name
from pipeline.import_contract_awards import fetch_all_contract_awards

BATCH_SIZE = 500

UPSERT_COLUMNS = (
    "url",
    "title",
    "description",
    "procurement_category",
    "procurement_method",
    "winner_company",
    "winner_address",
    "winner_city",
    "winner_province",
    "buyer_organization",
    "buyer_level",
    "award_value",
    "currency",
    "award_date",
    "contract_start_date",
    "contract_end_date",
    "delivery_region",
)


def upsert_contract_awards(session: Session, records: list[dict[str, Any]]) -> int:
    if not records:
        return 0

    table = ContractAward.__table__
    imported = 0
    for start in range(0, len(records), BATCH_SIZE):
        batch = records[start : start + BATCH_SIZE]
        stmt = insert(table).values(batch)
        update_cols = {column: stmt.excluded[column] for column in UPSERT_COLUMNS}
        stmt = stmt.on_conflict_do_update(
            index_elements=["source", "external_id"],
            set_=update_cols,
        )
        session.execute(stmt)
        session.commit()
        imported += len(batch)
    return imported


def match_contract_awards_to_companies(session: Session) -> dict[str, int]:
    indexes = build_company_indexes(session)
    matched = 0
    unmatched = 0
    exact = 0
    normalized = 0

    awards = session.scalars(select(ContractAward).where(ContractAward.winner_company != "")).all()

    for award in awards:
        company_id, method, confidence = match_vendor_name(award.winner_company, indexes)
        if company_id is None:
            unmatched += 1
            if award.company_id is not None or award.match_method:
                award.company_id = None
                award.match_method = "none"
                award.match_confidence = None
            continue

        matched += 1
        if method == "exact":
            exact += 1
        elif method == "normalized":
            normalized += 1
        award.company_id = company_id
        award.match_method = method
        award.match_confidence = confidence

    session.commit()
    return {
        "awards_matched": matched,
        "awards_unmatched": unmatched,
        "matches_exact": exact,
        "matches_normalized": normalized,
    }


def import_contract_awards(session: Session) -> dict[str, Any]:
    print("[ContractAwards] Fetching award records from all sources...")
    records = fetch_all_contract_awards()
    counts: dict[str, int] = {}
    for record in records:
        source = record["source"]
        counts[source] = counts.get(source, 0) + 1

    upserted = upsert_contract_awards(session, records)
    print(f"[ContractAwards] Upserted {upserted} rows")
    match_counts = match_contract_awards_to_companies(session)
    print(f"[ContractAwards] Matching complete: {match_counts}")
    return {
        "contract_awards_upserted": upserted,
        **counts,
        **match_counts,
    }
