"""Conservative normalization for Surrey permit applicant organizations."""

from __future__ import annotations

import re
from dataclasses import dataclass

from scraper.utils import clean_text

STATUS_MISSING = "missing"
STATUS_NORMALIZED_LEGAL_SUFFIX = "normalized_legal_suffix"
STATUS_NORMALIZED_BUSINESS_ADDRESS = "normalized_business_address"
STATUS_UNRESOLVED = "unresolved"

_ADDRESS_START = r"(?:unit\b|suite\b|\d{1,6}\b|[A-Z]\d[A-Z]\s?\d[A-Z]\d\b)"
_LEGAL_SUFFIX_RE = re.compile(
    rf"^(.+?\b(?:ltd|limited|inc|incorporated|corp|corporation|ulc|llp|lp|co)\.?)(?=\s+{_ADDRESS_START})",
    re.IGNORECASE,
)
_BUSINESS_ADDRESS_RE = re.compile(
    rf"^(.+?\b(?:construction|developments?|homes?|builders?|contracting|engineering|energy|projects?|group))(?=\s+{_ADDRESS_START})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SurreyApplicantNormalization:
    raw: str
    organization: str
    status: str

    @property
    def is_resolvable(self) -> bool:
        return bool(self.organization)


def normalize_surrey_applicant(value: object) -> SurreyApplicantNormalization:
    """Extract only a high-confidence organization name from Surrey's mixed field.

    The ArcGIS ApplicantOrganization value commonly appends a mailing address
    to the organization. Ambiguous values are deliberately left unresolved so
    they cannot create polluted canonical Company rows.
    """
    raw = clean_text(value)
    if not raw:
        return SurreyApplicantNormalization("", "", STATUS_MISSING)

    if re.search(r"\s+and\s+", raw, re.IGNORECASE):
        return SurreyApplicantNormalization(raw, "", STATUS_UNRESOLVED)

    for pattern, status in (
        (_LEGAL_SUFFIX_RE, STATUS_NORMALIZED_LEGAL_SUFFIX),
        (_BUSINESS_ADDRESS_RE, STATUS_NORMALIZED_BUSINESS_ADDRESS),
    ):
        match = pattern.match(raw)
        if match:
            organization = match.group(1).strip(" ,.;")
            if organization:
                return SurreyApplicantNormalization(raw, organization, status)

    return SurreyApplicantNormalization(raw, "", STATUS_UNRESOLVED)
