from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from db.models import Company, ContractAward
from pipeline.company_matching import build_company_indexes, match_vendor_name, normalize_vendor_name

INSERT_BATCH_SIZE = 500
MAX_NAME_LEN = 300

JUNK_MARKERS = frozenset(
    {
        "VARIOUS",
        "TBD",
        "N/A",
        "UNKNOWN",
        "TO BE DETERMINED",
    }
)


@dataclass
class _VendorGroup:
    spellings: Counter[str] = field(default_factory=Counter)


def _clamp_name(value: str) -> str:
    return (value or "").strip()[:MAX_NAME_LEN]


def _is_junk_vendor(name: str) -> bool:
    cleaned = (name or "").strip()
    if len(cleaned) < 3:
        return True
    upper = cleaned.upper()
    return any(marker in upper for marker in JUNK_MARKERS)


def _pick_canonical_name(spellings: Counter[str]) -> str:
    """Choose one vendor spelling per normalized group (dedup only, not company stats)."""
    ranked = sorted(
        spellings.items(),
        key=lambda item: (-item[1], -len(item[0]), item[0].lower()),
    )
    return _clamp_name(ranked[0][0])


def _collect_unmatched_vendor_groups(session: Session) -> dict[str, _VendorGroup]:
    rows = session.execute(
        select(ContractAward.winner_company).where(
            ContractAward.company_id.is_(None),
            ContractAward.winner_company != "",
        )
    ).scalars().all()

    groups: dict[str, _VendorGroup] = {}
    for raw_name in rows:
        name = (raw_name or "").strip()
        if not name:
            continue
        norm_key = normalize_vendor_name(name)
        if not norm_key:
            continue
        groups.setdefault(norm_key, _VendorGroup()).spellings[name] += 1
    return groups


def populate_companies_from_awards(
    session: Session,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Insert net-new companies for unmatched contract award vendors.

    Creates one company row per normalized vendor group. Does not update existing
    permit companies and does not populate award statistics (Phase C).
    """
    print("[AwardCompanies] Collecting unmatched contract award vendors...")
    groups = _collect_unmatched_vendor_groups(session)
    indexes = build_company_indexes(session)

    payload: list[dict[str, Any]] = []
    skipped_junk = 0
    skipped_existing = 0
    duplicate_spellings_collapsed = 0

    for norm_key, group in groups.items():
        duplicate_spellings_collapsed += max(0, len(group.spellings) - 1)
        canonical_name = _pick_canonical_name(group.spellings)
        if _is_junk_vendor(canonical_name):
            skipped_junk += 1
            continue

        company_id, _method, _confidence = match_vendor_name(canonical_name, indexes)
        if company_id is not None:
            skipped_existing += 1
            continue

        payload.append(
            {
                "name": canonical_name,
                "canonical_vendor_name": norm_key[:MAX_NAME_LEN],
                "data_sources": ["contract_awards"],
            }
        )

    payload.sort(key=lambda row: row["name"].lower())
    print(
        f"[AwardCompanies] Prepared {len(payload)} inserts "
        f"from {len(groups)} normalized groups "
        f"({duplicate_spellings_collapsed} duplicate spellings collapsed)"
    )

    gateway_stats: dict[str, int] = {}
    try:
        from pipeline.registry_gateway import get_registry_gateway

        payload, gateway_stats = get_registry_gateway(session).filter_award_populate_payload(
            payload,
            trigger_source="contract_awards",
        )
    except Exception:
        import logging

        logging.getLogger(__name__).exception(
            "[AwardCompanies] Registry Gateway filter failed — proceeding with legacy path"
        )

    if dry_run:
        print("[AwardCompanies] Dry run — no rows inserted")
        return {
            "dry_run": True,
            "normalized_groups": len(groups),
            "companies_created": 0,
            "companies_prepared": len(payload),
            "skipped_existing": skipped_existing,
            "skipped_junk": skipped_junk,
            "duplicate_spellings_collapsed": duplicate_spellings_collapsed,
            "gateway_blocked": gateway_stats.get("blocked", 0),
            "gateway_logged": gateway_stats.get("logged", 0),
        }

    table = Company.__table__
    created = 0
    for start in range(0, len(payload), INSERT_BATCH_SIZE):
        batch = payload[start : start + INSERT_BATCH_SIZE]
        stmt = insert(table).values(batch).on_conflict_do_nothing(index_elements=["name"])
        result = session.execute(stmt)
        session.commit()
        created += result.rowcount or 0

    print(f"[AwardCompanies] Insert complete: {created} new companies")
    return {
        "dry_run": False,
        "normalized_groups": len(groups),
        "companies_created": created,
        "companies_prepared": len(payload),
        "skipped_existing": skipped_existing,
        "skipped_junk": skipped_junk,
        "duplicate_spellings_collapsed": duplicate_spellings_collapsed,
        "gateway_blocked": gateway_stats.get("blocked", 0),
        "gateway_logged": gateway_stats.get("logged", 0),
    }
