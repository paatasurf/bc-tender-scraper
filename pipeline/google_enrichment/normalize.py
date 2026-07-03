"""Deterministic normalization helpers for Google place matching."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

_NAME_SUFFIXES = re.compile(
    r"\b(ltd\.?|limited|inc\.?|incorporated|corp\.?|corporation|llc|l\.l\.c\.?|"
    r"co\.?|company|plc|lp|l\.p\.?|holdings?|group|enterprises?|services?)\b",
    re.IGNORECASE,
)
_PAREN_REGION = re.compile(
    r"\s*[\(\[]\s*(bc|b\.c\.?|canada|vancouver|victoria|ab|on|alberta|ontario)\s*[\)\]]\s*",
    re.IGNORECASE,
)
_NON_ALNUM = re.compile(r"[^a-z0-9\s]")
_PHONE_DIGITS = re.compile(r"\D")

BC_PROVINCE_ALIASES = frozenset({"bc", "b.c.", "b.c", "british columbia"})
NON_BC_PROVINCE_MARKERS = frozenset(
    {
        ", on,",
        ", on ",
        ", ab,",
        ", ab ",
        ", alberta,",
        ", alberta ",
        ", ontario,",
        ", ontario ",
        ", qc,",
        ", quebec,",
    }
)


def normalize_company_name(name: str) -> str:
    text = name.lower().strip()
    text = _PAREN_REGION.sub(" ", text)
    text = _NAME_SUFFIXES.sub(" ", text)
    text = _NON_ALNUM.sub(" ", text)
    return " ".join(text.split())


def name_similarity(company_name: str, candidate_name: str) -> float:
    left = normalize_company_name(company_name)
    right = normalize_company_name(candidate_name)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


def normalize_phone(phone: str) -> str:
    digits = _PHONE_DIGITS.sub("", phone or "")
    if len(digits) == 11 and digits.startswith("1"):
        return digits[1:]
    return digits


def phone_match_score(company_phone: str, candidate_phone: str) -> float:
    left = normalize_phone(company_phone)
    right = normalize_phone(candidate_phone)
    if len(left) < 10 or len(right) < 10:
        return 0.0
    return 1.0 if left == right else 0.0


def normalize_province(province: str) -> str:
    text = (province or "").strip().lower()
    if text in BC_PROVINCE_ALIASES:
        return "bc"
    if text in {"ab", "alberta"}:
        return "ab"
    if text in {"on", "ontario"}:
        return "on"
    return text


def province_match_score(company_province: str, candidate_address: str) -> float:
    company = normalize_province(company_province) or "bc"
    address = (candidate_address or "").lower()
    if not address:
        return 0.0
    has_bc = ", bc" in address or "british columbia" in address
    if company == "bc" and has_bc:
        return 1.0
    return 0.0


def extract_city_from_address(address: str) -> str:
    parts = [part.strip() for part in (address or "").split(",") if part.strip()]
    if len(parts) >= 2:
        return parts[1]
    return ""


def normalize_city(city: str) -> str:
    text = (city or "").strip().lower()
    if text in {"van", "vancouver bc"}:
        return "vancouver"
    return text


def city_match_score(company_city: str, candidate_address: str) -> float:
    left = normalize_city(company_city)
    if not left:
        left = normalize_city(extract_city_from_address(candidate_address))
    right = normalize_city(extract_city_from_address(candidate_address))
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


def normalize_address(address: str) -> str:
    text = (address or "").lower().strip()
    text = _NON_ALNUM.sub(" ", text)
    return " ".join(text.split())


def address_similarity(company_address: str, candidate_address: str) -> float:
    left = normalize_address(company_address)
    right = normalize_address(candidate_address)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    left_street = left.split()[0:4]
    right_street = right.split()[0:4]
    return SequenceMatcher(None, " ".join(left_street), " ".join(right_street)).ratio()


def is_province_outside_bc(candidate_address: str) -> bool:
    address = f"{(candidate_address or '').lower()},"
    return any(marker in address for marker in NON_BC_PROVINCE_MARKERS)
