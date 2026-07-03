"""Deterministic heuristics for company vs person name classification."""

from __future__ import annotations

import re

MAX_NAME_LEN = 300

_PERSON_BIZ_MARKERS = re.compile(
    r"\b("
    r"inc|ltd|corp|llc|lp|limited|company|co|holdings|enterprises|group|"
    r"construction|builder|contractor|architect|engineering|design|consulting|"
    r"studio|homes|developments|services|industries|partners|associates|"
    r"consultants|builders|contractors|management|development|"
    r"renovation|restoration|plumbing|electric|mechanical|roofing|"
    r"concrete|excavating|demolition|painting|flooring|"
    r"dba|/"
    r")\b",
    re.I,
)
_PERSON_TOKEN = re.compile(r"^[A-Za-z][A-Za-z'\-]*$")


def is_probable_person_name(display_name: str) -> bool:
    """True when display_name looks like an individual, not a firm."""
    cleaned = (display_name or "").strip()[:MAX_NAME_LEN]
    if not cleaned or _PERSON_BIZ_MARKERS.search(cleaned):
        return False
    if any(ch.isdigit() for ch in cleaned):
        return False
    if "&" in cleaned or "," in cleaned:
        return False
    tokens = cleaned.split()
    if not (2 <= len(tokens) <= 3):
        return False
    return all(_PERSON_TOKEN.match(token) for token in tokens)
