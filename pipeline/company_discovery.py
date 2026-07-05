"""Deterministic company discovery from permit/tender records — no DB writes.

Evaluates structured fields, description patterns, and parsed applicant
identities in priority order. Never stops at person_skip on applicant alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from pipeline.identity_parser import RelationshipType, parse_identity

DISCOVERY_VERSION = "1.0.0"

PRIORITY_CONTRACTOR = 1
PRIORITY_STRUCTURED = 2
PRIORITY_DESCRIPTION = 3
PRIORITY_PARSED_APPLICANT = 4
PRIORITY_PLAIN_APPLICANT = 5

CONTRACTOR_FIELDS: tuple[str, ...] = (
    "contractor",
    "buildingcontractor",
    "builder",
    "general_contractor",
)

STRUCTURED_BUSINESS_FIELDS: tuple[str, ...] = (
    "business_name",
    "organization",
    "company",
    "employer",
)

DESCRIPTION_PATTERNS: tuple[tuple[re.Pattern[str], str, float], ...] = (
    (re.compile(r"\bDemo(?:lition)? Contractor:\s*([^(\n\r;]+)", re.I), "demo_contractor", 0.90),
    (re.compile(r"\bGeneral Contractor:\s*([^(\n\r;]+)", re.I), "general_contractor", 0.90),
    (re.compile(r"\bGC:\s*([^(\n\r;]+)", re.I), "general_contractor", 0.88),
    (re.compile(r"\bResidential Builder\s*[-–:]\s*([^(\n\r;]+)", re.I), "residential_builder", 0.90),
    (re.compile(r"\bBuilder:\s*([^(\n\r;]+)", re.I), "builder", 0.85),
    (re.compile(r"\bBuilding Contractor:\s*([^(\n\r;]+)", re.I), "building_contractor", 0.90),
    (re.compile(r"\bPrime Contractor:\s*([^(\n\r;]+)", re.I), "prime_contractor", 0.90),
    (re.compile(r"\bConstruction Manager:\s*([^(\n\r;]+)", re.I), "construction_manager", 0.88),
    (re.compile(r"\bHPO:\s*([^(\n\r;]+)", re.I), "hpo", 0.85),
    (re.compile(r"\bHomeowner Protection Office:\s*([^(\n\r;]+)", re.I), "hpo", 0.85),
    (re.compile(r"\bContractor:\s*([^(\n\r;]+)", re.I), "contractor", 0.80),
)

_PHONE_RE = re.compile(r"\(\d{3}\)\s*\d{3}[-\s]?\d{4}")
_QP_SUFFIX_RE = re.compile(r"\s+QP:.*$", re.I)
_NOTE_SUFFIX_RE = re.compile(r"\s+NOTE:.*$", re.I)


@dataclass(frozen=True)
class CompanyCandidate:
    value: str
    source: str
    priority: int
    confidence: float
    reason: str
    resolution_name: str

    def sort_key(self) -> tuple[int, float]:
        return (self.priority, -self.confidence)


@dataclass
class DiscoveryResult:
    candidates: list[CompanyCandidate] = field(default_factory=list)
    applicant_person: str | None = None
    selected: CompanyCandidate | None = None
    discovery_version: str = DISCOVERY_VERSION

    def ordered_candidates(self) -> list[CompanyCandidate]:
        return sorted(self.candidates, key=lambda c: c.sort_key())

    def to_dict(self) -> dict[str, Any]:
        return {
            "discovery_version": self.discovery_version,
            "applicant_person": self.applicant_person,
            "selected": {
                "value": self.selected.value,
                "source": self.selected.source,
                "priority": self.selected.priority,
                "confidence": self.selected.confidence,
                "reason": self.selected.reason,
                "resolution_name": self.selected.resolution_name,
            }
            if self.selected
            else None,
            "candidates": [
                {
                    "value": c.value,
                    "source": c.source,
                    "priority": c.priority,
                    "confidence": c.confidence,
                    "reason": c.reason,
                    "resolution_name": c.resolution_name,
                }
                for c in self.ordered_candidates()
            ],
        }


def _clean_description_company(raw: str) -> str:
    text = (raw or "").strip()
    text = _PHONE_RE.sub("", text)
    text = _QP_SUFFIX_RE.sub("", text)
    text = _NOTE_SUFFIX_RE.sub("", text)
    text = text.strip(" .,;")
    if len(text) <= 3:
        return ""
    return text[:300]


def _resolution_name_from_raw(raw: str) -> str | None:
    parsed = parse_identity(raw)
    target = parsed.resolution_target()
    if target:
        return target
    if parsed.relationship_type == RelationshipType.PLAIN_COMPANY:
        return parsed.business_name
    return None


def _add_candidate(
    candidates: list[CompanyCandidate],
    *,
    raw: str,
    source: str,
    priority: int,
    confidence: float,
    reason: str,
    seen: set[str],
) -> None:
    resolution_name = _resolution_name_from_raw(raw)
    if not resolution_name:
        return
    key = resolution_name.casefold()
    if key in seen:
        return
    seen.add(key)
    candidates.append(
        CompanyCandidate(
            value=raw.strip(),
            source=source,
            priority=priority,
            confidence=confidence,
            reason=reason,
            resolution_name=resolution_name,
        )
    )


def discover_companies(record: Mapping[str, Any]) -> DiscoveryResult:
    """Collect ranked company candidates from a permit-like record dict."""
    candidates: list[CompanyCandidate] = []
    seen: set[str] = set()
    applicant_person: str | None = None

    for field_name in CONTRACTOR_FIELDS:
        raw = (record.get(field_name) or "").strip()
        if raw:
            _add_candidate(
                candidates,
                raw=raw,
                source=field_name,
                priority=PRIORITY_CONTRACTOR,
                confidence=1.0,
                reason=f"structured contractor field ({field_name})",
                seen=seen,
            )

    for field_name in STRUCTURED_BUSINESS_FIELDS:
        if field_name in CONTRACTOR_FIELDS:
            continue
        raw = (record.get(field_name) or "").strip()
        if raw:
            _add_candidate(
                candidates,
                raw=raw,
                source=field_name,
                priority=PRIORITY_STRUCTURED,
                confidence=0.95,
                reason=f"structured business field ({field_name})",
                seen=seen,
            )

    description = (record.get("description") or "").strip()
    for pattern, label, confidence in DESCRIPTION_PATTERNS:
        for match in pattern.findall(description):
            cleaned = _clean_description_company(match)
            if cleaned:
                _add_candidate(
                    candidates,
                    raw=cleaned,
                    source=f"description:{label}",
                    priority=PRIORITY_DESCRIPTION,
                    confidence=confidence,
                    reason=f"description pattern ({label})",
                    seen=seen,
                )

    applicant = (record.get("applicant") or "").strip()
    if applicant:
        parsed = parse_identity(applicant)
        if parsed.person_name and not parsed.business_name:
            applicant_person = parsed.person_name
        if parsed.business_name and parsed.relationship_type != RelationshipType.PLAIN_COMPANY:
            _add_candidate(
                candidates,
                raw=parsed.business_name,
                source=f"applicant_parsed:{parsed.relationship_type.value}",
                priority=PRIORITY_PARSED_APPLICANT,
                confidence=parsed.parse_confidence,
                reason=f"parsed business from applicant ({parsed.relationship_type.value})",
                seen=seen,
            )
        elif parsed.relationship_type == RelationshipType.PLAIN_COMPANY:
            _add_candidate(
                candidates,
                raw=applicant,
                source="applicant",
                priority=PRIORITY_PLAIN_APPLICANT,
                confidence=parsed.parse_confidence,
                reason="plain company applicant",
                seen=seen,
            )

    result = DiscoveryResult(
        candidates=candidates,
        applicant_person=applicant_person,
    )
    ordered = result.ordered_candidates()
    if ordered:
        result.selected = ordered[0]
    return result
