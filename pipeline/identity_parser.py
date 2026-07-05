"""Deterministic identity parsing — RAW → PARSED (no DB writes).

Extracts structured person/business components from composite source strings
(e.g. permit applicants) before canonical company resolution.

Constitution: parsing is Python-only, deterministic, and reproducible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from pipeline.company_name_heuristics import is_probable_person_name

PARSER_VERSION = "1.0.0"
MAX_IDENTITY_LEN = 300

# --- relationship types -------------------------------------------------------

class RelationshipType(StrEnum):
    DBA = "dba"
    OPERATING_AS = "operating_as"
    CARE_OF = "care_of"
    JOINT_VENTURE = "joint_venture"
    PARTNERSHIP = "partnership"
    TRADE_NAME = "trade_name"  # slash-separated legal / trade
    PLAIN_PERSON = "plain_person"
    PLAIN_COMPANY = "plain_company"
    UNPARSEABLE = "unparseable"


# --- confidence constants (deterministic) ---------------------------------------

CONFIDENCE_EXPLICIT = 1.0
CONFIDENCE_STRUCTURED = 0.95
CONFIDENCE_HEURISTIC = 0.85
CONFIDENCE_PLAIN = 0.80
CONFIDENCE_NONE = 0.0

# --- pattern registry (order matters — most specific first) --------------------

@dataclass(frozen=True)
class _PatternRule:
    relationship: RelationshipType
    confidence: float
    pattern: re.Pattern[str]
    person_group: int | None = None
    business_group: int = 2


# Each tuple: (relationship, confidence, regex, person_group, business_group)
_PATTERN_SPECS: tuple[tuple[RelationshipType, float, str, int | None, int], ...] = (
    (RelationshipType.DBA, CONFIDENCE_EXPLICIT, r"^(.+?)\s+DBA:\s*(.+)$", 1, 2),
    (RelationshipType.DBA, CONFIDENCE_EXPLICIT, r"^(.+?)\s+DBA\s+(.+)$", 1, 2),
    (RelationshipType.DBA, CONFIDENCE_EXPLICIT, r"^(.+?)\s+Doing Business As\s+(.+)$", 1, 2),
    (RelationshipType.DBA, CONFIDENCE_EXPLICIT, r"^(.+?)\s+D/B/A\s+(.+)$", 1, 2),
    (RelationshipType.OPERATING_AS, CONFIDENCE_EXPLICIT, r"^(.+?)\s+O/A\s+(.+)$", 1, 2),
    (RelationshipType.OPERATING_AS, CONFIDENCE_EXPLICIT, r"^(.+?)\s+Operating As\s+(.+)$", 1, 2),
    (RelationshipType.CARE_OF, CONFIDENCE_STRUCTURED, r"^(.+?)\s+c/o\s+(.+)$", 1, 2),
    (RelationshipType.CARE_OF, CONFIDENCE_STRUCTURED, r"^(.+?)\s+care of\s+(.+)$", 1, 2),
    (RelationshipType.JOINT_VENTURE, CONFIDENCE_STRUCTURED, r"^(.+?)\s+Joint Venture\s+(.+)$", 1, 2),
    (RelationshipType.PARTNERSHIP, CONFIDENCE_HEURISTIC, r"^(.+?)\s+&\s+(.+)$", 1, 2),
    (RelationshipType.TRADE_NAME, CONFIDENCE_STRUCTURED, r"^(.+?)\s*/\s*(.+)$", 1, 2),
)

_COMPILED_RULES: tuple[_PatternRule, ...] = tuple(
    _PatternRule(
        relationship=rel,
        confidence=conf,
        pattern=re.compile(pat, re.I),
        person_group=pg,
        business_group=bg,
    )
    for rel, conf, pat, pg, bg in _PATTERN_SPECS
)


@dataclass(frozen=True)
class ParsedIdentity:
    """Structured identity derived from a raw source string."""

    raw_identity: str
    person_name: str | None
    business_name: str | None
    relationship_type: RelationshipType
    parser_version: str
    parse_confidence: float
    secondary_business_name: str | None = None

    def resolution_target(self) -> str | None:
        """Business name for Company Resolver — never a person name."""
        return self.business_name

    def to_dict(self) -> dict[str, str | float | None]:
        return {
            "raw_identity": self.raw_identity,
            "person_name": self.person_name,
            "business_name": self.business_name,
            "secondary_business_name": self.secondary_business_name,
            "relationship_type": self.relationship_type.value,
            "parser_version": self.parser_version,
            "parse_confidence": self.parse_confidence,
        }


def _clamp(raw: str) -> str:
    return (raw or "").strip()[:MAX_IDENTITY_LEN]


def _clean_part(value: str) -> str:
    return (value or "").strip().strip(",;")


def _match_structured(cleaned: str) -> ParsedIdentity | None:
    for rule in _COMPILED_RULES:
        match = rule.pattern.match(cleaned)
        if not match:
            continue

        person = (
            _clean_part(match.group(rule.person_group))
            if rule.person_group is not None
            else None
        )
        business = _clean_part(match.group(rule.business_group))

        if rule.relationship == RelationshipType.PARTNERSHIP:
            return ParsedIdentity(
                raw_identity=cleaned,
                person_name=None,
                business_name=business or person,
                secondary_business_name=person if person and person != business else None,
                relationship_type=rule.relationship,
                parser_version=PARSER_VERSION,
                parse_confidence=rule.confidence,
            )

        if rule.relationship == RelationshipType.TRADE_NAME:
            left_is_person = is_probable_person_name(person or "")
            if left_is_person:
                return ParsedIdentity(
                    raw_identity=cleaned,
                    person_name=person,
                    business_name=business,
                    relationship_type=RelationshipType.TRADE_NAME,
                    parser_version=PARSER_VERSION,
                    parse_confidence=rule.confidence,
                )
            return ParsedIdentity(
                raw_identity=cleaned,
                person_name=None,
                business_name=business or person,
                secondary_business_name=person if person and person != business else None,
                relationship_type=RelationshipType.TRADE_NAME,
                parser_version=PARSER_VERSION,
                parse_confidence=rule.confidence,
            )

        return ParsedIdentity(
            raw_identity=cleaned,
            person_name=person,
            business_name=business,
            relationship_type=rule.relationship,
            parser_version=PARSER_VERSION,
            parse_confidence=rule.confidence,
        )

    return None


def parse_identity(raw: str) -> ParsedIdentity:
    """Parse a raw identity string into structured components.

    Pure function — no database access. Same input always yields same output.
    """
    cleaned = _clamp(raw)
    if not cleaned:
        return ParsedIdentity(
            raw_identity="",
            person_name=None,
            business_name=None,
            relationship_type=RelationshipType.UNPARSEABLE,
            parser_version=PARSER_VERSION,
            parse_confidence=CONFIDENCE_NONE,
        )

    structured = _match_structured(cleaned)
    if structured is not None:
        return structured

    if is_probable_person_name(cleaned):
        return ParsedIdentity(
            raw_identity=cleaned,
            person_name=cleaned,
            business_name=None,
            relationship_type=RelationshipType.PLAIN_PERSON,
            parser_version=PARSER_VERSION,
            parse_confidence=CONFIDENCE_PLAIN,
        )

    return ParsedIdentity(
        raw_identity=cleaned,
        person_name=None,
        business_name=cleaned,
        relationship_type=RelationshipType.PLAIN_COMPANY,
        parser_version=PARSER_VERSION,
        parse_confidence=CONFIDENCE_PLAIN,
    )
