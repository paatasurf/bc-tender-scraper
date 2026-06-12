from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Company
from pipeline.company_classification import parse_name

SUFFIX_RE = re.compile(
    r"\b(incorporated|inc|ltd|limited|corp|corporation|llc|lp|co|company|holdings|enterprises|group|the)\b",
    re.I,
)


def normalize_vendor_name(raw: str) -> str:
    if not raw:
        return ""
    parsed = parse_name(raw)
    name = (parsed["dba"] or parsed["legal"] or raw).strip().lower()
    name = re.sub(r"[''`]", "", name)
    name = SUFFIX_RE.sub(" ", name)
    name = re.sub(r"[^a-z0-9& ]", " ", name)
    return re.sub(r"\s+", " ", name).strip().replace(" ", "")


@dataclass
class CompanyIndexes:
    exact: dict[str, int]
    normalized: dict[str, int]


def build_company_indexes(session: Session) -> CompanyIndexes:
    exact: dict[str, int] = {}
    normalized: dict[str, int] = {}
    for company_id, name in session.execute(select(Company.id, Company.name)).all():
        if not name:
            continue
        exact.setdefault(name, company_id)
        for candidate in (name, parse_name(name)["dba"], parse_name(name)["legal"]):
            key = normalize_vendor_name(candidate)
            if key and key not in normalized:
                normalized[key] = company_id
    return CompanyIndexes(exact=exact, normalized=normalized)


def match_vendor_name(
    vendor: str,
    indexes: CompanyIndexes,
) -> tuple[int | None, str, float | None]:
    cleaned = (vendor or "").strip()
    if not cleaned:
        return None, "none", None

    if cleaned in indexes.exact:
        return indexes.exact[cleaned], "exact", 1.0

    key = normalize_vendor_name(cleaned)
    if key and key in indexes.normalized:
        return indexes.normalized[key], "normalized", 0.95

    return None, "none", None
