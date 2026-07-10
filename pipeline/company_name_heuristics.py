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

# Single-word parenthetical nickname, e.g. "Yi Chieh (Ashanti) Lee".
_PARENTHETICAL_NICKNAME = re.compile(r"\s*\([A-Za-z][A-Za-z'\-]*\)\s*")


def _strip_parenthetical_nickname(display_name: str) -> str:
    """Return the name with a single-word parenthetical nickname removed.

    Examples:
        "Yi Chieh (Ashanti) Lee" -> "Yi Chieh Lee"
        "John Smith"             -> "John Smith"
        "ABC (Vancouver) Ltd"    -> "ABC (Vancouver) Ltd" (not a nickname)
    """
    return _PARENTHETICAL_NICKNAME.sub(" ", display_name).strip()


def is_probable_person_name(display_name: str) -> bool:
    """True when display_name looks like an individual, not a firm.

    Handles plain "First Last" / "First Middle Last" names as well as
    names with a single-word parenthetical nickname such as
    "Yi Chieh (Ashanti) Lee".
    """
    cleaned = (display_name or "").strip()[:MAX_NAME_LEN]
    if not cleaned or _PERSON_BIZ_MARKERS.search(cleaned):
        return False
    if any(ch.isdigit() for ch in cleaned):
        return False
    if "&" in cleaned or "," in cleaned:
        return False

    # Evaluate both the raw name and the name with a parenthetical nickname
    # stripped.  Only classify as a person if the resulting tokens are valid
    # person-name tokens, preserving the existing 2-3 token rule.
    for candidate in (cleaned, _strip_parenthetical_nickname(cleaned)):
        tokens = candidate.split()
        if 2 <= len(tokens) <= 3 and all(_PERSON_TOKEN.match(token) for token in tokens):
            return True
    return False
