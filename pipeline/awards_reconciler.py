"""P2-03 — Match closed tenders to contract_awards (precision-first, no fuzzy)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Type

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.lifecycle_constants import LIFECYCLE_STATUS_AWARDED, LIFECYCLE_STATUS_CLOSED
from db.models import ArchTender, CommercialTender, ContractAward, Tender
from pipeline.lifecycle_resolver import TENDER_LIFECYCLE_MODELS, has_manual_lifecycle_override

logger = logging.getLogger(__name__)

# Stored in award_match_confidence (float column); 1.0 = high-confidence auto-mark.
AWARD_MATCH_CONFIDENCE_HIGH = 1.0

MATCH_SIGNAL_EZ899_CODE = "ez899_code"
MATCH_SIGNAL_TITLE_BUYER = "title_buyer"

SOLICITATION_CODE_RE = re.compile(r"\b(EZ899-\d+)\b", re.I)

TENDER_BUYER_ATTR: dict[Type, str] = {
    Tender: "organization",
    CommercialTender: "company",
    ArchTender: "company",
}


@dataclass(frozen=True)
class AwardMatch:
    award_id: int
    signal: str
    award_date: str


@dataclass
class AwardIndexes:
    by_code: dict[str, list[ContractAward]]
    by_title_buyer: dict[tuple[str, str], list[ContractAward]]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_match_text(value: str | None) -> str:
    if not value:
        return ""
    text = value.lower().strip()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def extract_solicitation_codes(*texts: str | None) -> set[str]:
    codes: set[str] = set()
    for text in texts:
        if not text:
            continue
        for match in SOLICITATION_CODE_RE.finditer(text):
            codes.add(match.group(1).upper())
    return codes


def parse_award_datetime(value: str | None, *, fallback: datetime) -> datetime:
    if not value:
        return fallback
    raw = str(value).strip()[:10]
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d")
        return parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return fallback


def build_award_indexes(awards: list[ContractAward]) -> AwardIndexes:
    by_code: dict[str, list[ContractAward]] = {}
    by_title_buyer: dict[tuple[str, str], list[ContractAward]] = {}

    for award in awards:
        for code in extract_solicitation_codes(award.title, award.description):
            by_code.setdefault(code, []).append(award)

        key = (normalize_match_text(award.title), normalize_match_text(award.buyer_organization))
        if key[0] and key[1]:
            by_title_buyer.setdefault(key, []).append(award)

    return AwardIndexes(by_code=by_code, by_title_buyer=by_title_buyer)


def _resolve_unique_award(
    candidates: list[ContractAward],
    *,
    closed_at: datetime | None,
    now: datetime,
) -> ContractAward | None:
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    reference = closed_at
    if reference is not None and reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)

    if reference is not None:
        after_close = [
            award
            for award in candidates
            if parse_award_datetime(award.award_date, fallback=now) >= reference
        ]
        if len(after_close) == 1:
            return after_close[0]

    return None


def find_award_match(
    *,
    title: str,
    buyer: str,
    closed_at: datetime | None,
    indexes: AwardIndexes,
    now: datetime,
) -> AwardMatch | None:
    tender_codes = extract_solicitation_codes(title)
    code_candidates: list[ContractAward] = []
    seen_award_ids: set[int] = set()
    for code in sorted(tender_codes):
        for award in indexes.by_code.get(code, []):
            if award.id not in seen_award_ids:
                seen_award_ids.add(award.id)
                code_candidates.append(award)

    code_award = _resolve_unique_award(code_candidates, closed_at=closed_at, now=now)
    if code_award is not None:
        return AwardMatch(
            award_id=int(code_award.id),
            signal=MATCH_SIGNAL_EZ899_CODE,
            award_date=code_award.award_date or "",
        )

    title_key = (normalize_match_text(title), normalize_match_text(buyer))
    if title_key[0] and title_key[1]:
        title_candidates = indexes.by_title_buyer.get(title_key, [])
        title_award = _resolve_unique_award(title_candidates, closed_at=closed_at, now=now)
        if title_award is not None:
            return AwardMatch(
                award_id=int(title_award.id),
                signal=MATCH_SIGNAL_TITLE_BUYER,
                award_date=title_award.award_date or "",
            )

    return None


def _has_match_candidates(
    *,
    title: str,
    buyer: str,
    indexes: AwardIndexes,
) -> bool:
    tender_codes = extract_solicitation_codes(title)
    for code in tender_codes:
        if indexes.by_code.get(code):
            return True
    title_key = (normalize_match_text(title), normalize_match_text(buyer))
    return bool(title_key[0] and title_key[1] and indexes.by_title_buyer.get(title_key))


def _apply_award_match(row: Any, match: AwardMatch, *, now: datetime) -> None:
    row.lifecycle_status = LIFECYCLE_STATUS_AWARDED
    row.is_open = False
    row.award_id = match.award_id
    row.award_match_confidence = AWARD_MATCH_CONFIDENCE_HIGH
    row.awarded_at = parse_award_datetime(match.award_date, fallback=now)


def _empty_table_summary() -> dict[str, int]:
    return {
        MATCH_SIGNAL_EZ899_CODE: 0,
        MATCH_SIGNAL_TITLE_BUYER: 0,
        "skipped_override": 0,
        "skipped_already_awarded": 0,
        "skipped_not_closed": 0,
        "skipped_ambiguous": 0,
        "skipped_no_match": 0,
    }


def _reconcile_table(
    session: Session,
    model: Type[Tender] | Type[CommercialTender] | Type[ArchTender],
    *,
    indexes: AwardIndexes,
    now: datetime,
) -> dict[str, int]:
    summary = _empty_table_summary()
    buyer_attr = TENDER_BUYER_ATTR[model]
    rows = session.scalars(select(model)).all()

    for row in rows:
        if row.lifecycle_status != LIFECYCLE_STATUS_CLOSED:
            if row.lifecycle_status == LIFECYCLE_STATUS_AWARDED:
                summary["skipped_already_awarded"] += 1
            else:
                summary["skipped_not_closed"] += 1
            continue

        if has_manual_lifecycle_override(row.lifecycle_status_override):
            summary["skipped_override"] += 1
            continue

        title = str(getattr(row, "title", "") or "")
        buyer = str(getattr(row, buyer_attr, "") or "")
        closed_at = getattr(row, "closed_at", None) or getattr(row, "closing_at", None)
        match = find_award_match(
            title=title,
            buyer=buyer,
            closed_at=closed_at,
            indexes=indexes,
            now=now,
        )
        if match is None:
            if _has_match_candidates(title=title, buyer=buyer, indexes=indexes):
                summary["skipped_ambiguous"] += 1
            else:
                summary["skipped_no_match"] += 1
            continue

        _apply_award_match(row, match, now=now)
        summary[match.signal] += 1

    return summary


def reconcile_awards(
    session: Session,
    *,
    now: datetime | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Link closed tenders to contract_awards when high-confidence signals match."""
    reconciled_at = now or _utc_now()
    awards = list(session.scalars(select(ContractAward)).all())
    indexes = build_award_indexes(awards)

    tables: dict[str, dict[str, int]] = {}
    totals = _empty_table_summary()

    for model, table_name in TENDER_LIFECYCLE_MODELS:
        table_summary = _reconcile_table(session, model, indexes=indexes, now=reconciled_at)
        tables[table_name] = table_summary
        for key, count in table_summary.items():
            totals[key] += count

    if commit:
        session.commit()

    payload = {
        "reconciled_at": reconciled_at.isoformat(),
        "award_count": len(awards),
        "tables": tables,
        "totals": totals,
    }
    logger.info("[AwardsReconciler] summary: %s", payload)
    return payload
