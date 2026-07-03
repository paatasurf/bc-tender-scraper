"""Central company resolution for all ingestion pipelines."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from db.company_canonical_constants import (
    CONFIDENCE_DBA_EXPLICIT,
    ENTITY_ROLE_APPLICANT_ALIAS,
    ENTITY_ROLE_CANONICAL,
    ENTITY_ROLE_STANDALONE,
    FORCED_CANONICAL_IDS_BY_KEY,
)
from db.models import Company
from pipeline.company_name_heuristics import is_probable_person_name
from pipeline.company_canonical_merge import resolve_company_name
from pipeline.company_matching import normalize_vendor_name

logger = logging.getLogger(__name__)

MAX_NAME_LEN = 300

RESOLUTION_STATUS_RESOLVED = "resolved"
RESOLUTION_STATUS_REVIEW = "review"
RESOLUTION_STATUS_PERSON_SKIP = "person_skip"

INCORPORATED_RE = re.compile(
    r"\b(incorporated|inc|ltd|limited|corp|corporation|llc|lp)\b",
    re.I,
)
BC_MARKER_RE = re.compile(
    r"\b(bc|b\.c\.|british columbia|vancouver|victoria|surrey|burnaby|richmond|"
    r"coquitlam|langley|delta|north vancouver|west vancouver|kelowna|kamloops|"
    r"nanaimo|abbotsford|chilliwack|prince george)\b",
    re.I,
)

CONFIDENCE_INCORPORATED_BC = 1.0
CONFIDENCE_BC_OTHER = 0.9
MIN_DBA_FAMILY_PREFIX_LEN = 4


@dataclass
class CompanyResolution:
    company_id: int | None
    display_name: str
    canonical_key: str
    raw_name: str
    signatory: str
    confidence: float
    status: str
    method: str
    created: bool = False
    has_dba: bool = False


@dataclass
class CompanyResolver:
    session: Session
    _key_to_ids: dict[str, set[int]] = field(default_factory=dict)
    _id_to_row: dict[int, Company] = field(default_factory=dict)
    _loaded: bool = False
    review_log: list[dict[str, Any]] = field(default_factory=list)
    confidence_log: list[dict[str, Any]] = field(default_factory=list)

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        for company in self.session.scalars(select(Company)).all():
            self._id_to_row[int(company.id)] = company
            for candidate in (company.name, company.display_name, company.canonical_vendor_name):
                key = normalize_vendor_name(candidate or "")
                if not key:
                    continue
                self._key_to_ids.setdefault(key, set()).add(int(company.id))
            resolved = resolve_company_name(company.name or "")
            if resolved:
                self._key_to_ids.setdefault(resolved.canonical_key, set()).add(int(company.id))
        self._loaded = True

    def reload(self) -> None:
        self._key_to_ids.clear()
        self._id_to_row.clear()
        self._loaded = False
        self._ensure_loaded()

    def resolve(
        self,
        raw_name: str,
        *,
        source: str,
        city: str = "",
        province: str = "BC",
        create_if_missing: bool = True,
    ) -> CompanyResolution:
        self._ensure_loaded()
        cleaned = (raw_name or "").strip()[:MAX_NAME_LEN]
        if not cleaned:
            return CompanyResolution(
                company_id=None,
                display_name="",
                canonical_key="",
                raw_name=cleaned,
                signatory="",
                confidence=0.0,
                status=RESOLUTION_STATUS_PERSON_SKIP,
                method="empty",
            )

        parsed = resolve_company_name(cleaned)
        if parsed is None:
            return CompanyResolution(
                company_id=None,
                display_name="",
                canonical_key="",
                raw_name=cleaned,
                signatory="",
                confidence=0.0,
                status=RESOLUTION_STATUS_PERSON_SKIP,
                method="unparsed",
            )

        if not parsed.has_dba and is_probable_person_name(parsed.display_name):
            return CompanyResolution(
                company_id=None,
                display_name=parsed.display_name,
                canonical_key=parsed.canonical_key,
                raw_name=cleaned,
                signatory="",
                confidence=0.0,
                status=RESOLUTION_STATUS_PERSON_SKIP,
                method="probable_person",
                has_dba=False,
            )

        confidence, method = compute_bc_confidence(
            parsed.display_name,
            city=city,
            province=province,
            has_dba=parsed.has_dba,
        )
        if confidence < CONFIDENCE_INCORPORATED_BC:
            entry = {
                "raw_name": cleaned,
                "display_name": parsed.display_name,
                "source": source,
                "confidence": confidence,
                "method": method,
            }
            self.confidence_log.append(entry)
            logger.info("[CompanyResolver] BC confidence 0.9: %s", entry)

        key = parsed.canonical_key
        existing_ids = self._collect_candidate_ids(parsed)
        forced_id = FORCED_CANONICAL_IDS_BY_KEY.get(key)
        has_canonical = self._has_canonical_candidate(existing_ids, forced_id)

        status = RESOLUTION_STATUS_RESOLVED
        if len(existing_ids) > 1:
            if has_canonical:
                self.review_log.append(
                    {
                        "raw_name": cleaned,
                        "canonical_key": key,
                        "company_ids": sorted(existing_ids),
                        "source": source,
                        "reason": "canonical_key_family_match",
                    }
                )
                logger.info(
                    "[CompanyResolver] family match canonical_key=%s ids=%s raw=%r",
                    key,
                    sorted(existing_ids),
                    cleaned,
                )
            else:
                status = RESOLUTION_STATUS_REVIEW
                self.review_log.append(
                    {
                        "raw_name": cleaned,
                        "canonical_key": key,
                        "company_ids": sorted(existing_ids),
                        "source": source,
                        "reason": "canonical_key_conflict",
                    }
                )
                logger.warning(
                    "[CompanyResolver] conflict review canonical_key=%s ids=%s raw=%r",
                    key,
                    sorted(existing_ids),
                    cleaned,
                )

        company_id: int | None = None
        created = False
        if status == RESOLUTION_STATUS_REVIEW:
            if existing_ids:
                company_id = _pick_primary_id(self.session, existing_ids, forced_id=forced_id)
        elif existing_ids:
            company_id = _pick_primary_id(self.session, existing_ids, forced_id=forced_id)
        elif create_if_missing:
            insert_name = _clamp_name(parsed.display_name)
            company_id, created = self._create_company(
                name=insert_name,
                display_name=parsed.display_name,
                canonical_key=key,
                signatory=parsed.signatory if parsed.has_dba else "",
                confidence=confidence,
                method=method,
            )

        if company_id is not None:
            self._key_to_ids.setdefault(key, set()).add(company_id)

        return CompanyResolution(
            company_id=company_id,
            display_name=parsed.display_name,
            canonical_key=key,
            raw_name=cleaned,
            signatory=parsed.signatory if parsed.has_dba else "",
            confidence=confidence if parsed.has_dba else min(confidence, CONFIDENCE_BC_OTHER),
            status=status,
            method=method if status == RESOLUTION_STATUS_RESOLVED else f"{method}_conflict_review",
            created=created,
            has_dba=parsed.has_dba,
        )

    def _collect_candidate_ids(self, parsed) -> set[int]:
        """Exact key matches plus canonical rows in the same DBA trade-name family."""
        ids: set[int] = set(self._key_to_ids.get(parsed.canonical_key, set()))
        forced_id = FORCED_CANONICAL_IDS_BY_KEY.get(parsed.canonical_key)
        if forced_id is not None:
            ids.add(forced_id)

        if parsed.has_dba and len(parsed.canonical_key) >= MIN_DBA_FAMILY_PREFIX_LEN:
            trade_key = parsed.canonical_key
            for company in self._id_to_row.values():
                if company.entity_role != ENTITY_ROLE_CANONICAL:
                    continue
                display_key = normalize_vendor_name(company.display_name or company.name or "")
                if not display_key:
                    continue
                if display_key.startswith(trade_key) or trade_key.startswith(display_key):
                    ids.add(int(company.id))
        return ids

    def _has_canonical_candidate(
        self,
        existing_ids: set[int],
        forced_id: int | None,
    ) -> bool:
        if forced_id is not None:
            return True
        for company_id in existing_ids:
            row = self._id_to_row.get(company_id)
            if row is not None and row.entity_role == ENTITY_ROLE_CANONICAL:
                return True
        return False

    def _create_company(
        self,
        *,
        name: str,
        display_name: str,
        canonical_key: str,
        signatory: str,
        confidence: float,
        method: str,
    ) -> tuple[int, bool]:
        existing = self.session.execute(
            select(Company.id).where(func.lower(Company.name) == name.lower())
        ).scalar_one_or_none()
        if existing is not None:
            return int(existing), False

        company = Company(
            name=name,
            display_name=display_name,
            canonical_vendor_name=canonical_key,
            entity_role=ENTITY_ROLE_STANDALONE,
            applicant_signatory=signatory,
            canonical_merge_confidence=confidence,
            canonical_merge_method=method,
        )
        self.session.add(company)
        self.session.flush()
        self._id_to_row[int(company.id)] = company
        return int(company.id), True


def _clamp_name(value: str) -> str:
    return (value or "").strip()[:MAX_NAME_LEN]


def compute_bc_confidence(
    display_name: str,
    *,
    city: str = "",
    province: str = "BC",
    has_dba: bool = False,
) -> tuple[float, str]:
    """BC resolution confidence: incorporated+BC=1.0, other BC=0.9."""
    if has_dba:
        return CONFIDENCE_DBA_EXPLICIT, "dba_explicit"

    incorporated = bool(INCORPORATED_RE.search(display_name or ""))
    is_bc = _is_bc_entity(display_name, city=city, province=province)
    if incorporated and is_bc:
        return CONFIDENCE_INCORPORATED_BC, "incorporated_bc"
    if is_bc:
        return CONFIDENCE_BC_OTHER, "bc_other"
    return CONFIDENCE_BC_OTHER, "non_bc_default"


def _is_bc_entity(display_name: str, *, city: str, province: str) -> bool:
    blob = f"{display_name} {city} {province}"
    if province.strip().upper() in {"BC", "BRITISH COLUMBIA"}:
        return True
    return bool(BC_MARKER_RE.search(blob))


def _pick_primary_id(
    session: Session,
    company_ids: set[int],
    *,
    forced_id: int | None = None,
) -> int:
    if forced_id is not None and forced_id in company_ids:
        return forced_id

    companies = session.scalars(
        select(Company).where(Company.id.in_(sorted(company_ids)))
    ).all()
    if not companies:
        return sorted(company_ids)[0]

    # Follow alias → canonical; then prefer canonical entity_role over standalone.
    targets: dict[int, Company] = {}
    for company in companies:
        if (
            company.entity_role == ENTITY_ROLE_APPLICANT_ALIAS
            and company.canonical_company_id is not None
        ):
            canonical = session.get(Company, int(company.canonical_company_id))
            if canonical is not None:
                targets[int(canonical.id)] = canonical
                continue
        targets[int(company.id)] = company

    canonical_rows = [c for c in targets.values() if c.entity_role == ENTITY_ROLE_CANONICAL]
    pool = canonical_rows if canonical_rows else list(targets.values())

    ranked = sorted(
        pool,
        key=lambda row: (
            1 if row.entity_role == ENTITY_ROLE_CANONICAL else 0,
            float(row.total_value or 0) + float(row.total_award_value or 0),
            int(row.total_projects or 0),
            -int(row.id),
        ),
        reverse=True,
    )
    return int(ranked[0].id)


def resolve_company(
    session: Session,
    raw_name: str,
    *,
    source: str,
    city: str = "",
    province: str = "BC",
    create_if_missing: bool = True,
) -> CompanyResolution:
    """Resolve a raw vendor/applicant string to a canonical company_id."""
    return CompanyResolver(session).resolve(
        raw_name,
        source=source,
        city=city,
        province=province,
        create_if_missing=create_if_missing,
    )


def assign_permit_company_ids(session: Session, *, source: str | None = None) -> dict[str, int]:
    """Backfill permits.company_id using resolver (for batch jobs)."""
    from db.models import Permit

    resolver = CompanyResolver(session)
    stats = {"resolved": 0, "skipped": 0, "review": 0}
    query = select(Permit.id, Permit.applicant, Permit.contractor, Permit.city, Permit.source)
    if source:
        query = query.where(Permit.source == source)

    for permit_id, applicant, contractor, city, permit_source in session.execute(query).all():
        raw = (applicant or contractor or "").strip()
        if not raw:
            stats["skipped"] += 1
            continue
        resolution = resolver.resolve(
            raw,
            source=f"permits:{permit_source or 'unknown'}",
            city=city or "",
        )
        if resolution.company_id is None:
            stats["skipped"] += 1
            continue
        if resolution.status == RESOLUTION_STATUS_REVIEW:
            stats["review"] += 1
        session.execute(
            update(Permit)
            .where(Permit.id == int(permit_id))
            .values(
                company_id=resolution.company_id,
                canonical_merge_confidence=resolution.confidence,
                canonical_merge_method=resolution.method,
            )
        )
        stats["resolved"] += 1
    session.commit()
    return stats
