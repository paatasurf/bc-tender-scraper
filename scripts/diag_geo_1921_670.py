"""Diagnostic: geographic overlap for company 1921 vs 670."""
from __future__ import annotations


from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db.connection import get_session
from db.db_safety import guard_readonly_db
_SCRIPT = Path(__file__).name
from db.models import Company
from pipeline.cip_builder import get_cip
from pipeline.competitive_intel.overlap import (
    _city_level_tokens,
    _jaccard,
    _neighborhood_fallback_tokens,
    _resolve_primary_city,
    geographic_overlap_raw,
)
from pipeline.scoring.explain import weighted_fit

SUBJECT_ID = 1921
PEER_ID = 670


def main() -> None:
    guard_readonly_db(_SCRIPT)
    session = get_session()
    try:
        subject = session.get(Company, SUBJECT_ID)
        peer = session.get(Company, PEER_ID)
        if subject is None or peer is None:
            raise SystemExit(f"Missing company rows: {SUBJECT_ID=}, {PEER_ID=}")

        subject_cip = get_cip(session, company_id=SUBJECT_ID, kind="construction", refresh=False)
        peer_cip = get_cip(session, company_id=PEER_ID, kind="construction", refresh=False)

        cities_s = _city_level_tokens(subject_cip, subject)
        cities_p = _city_level_tokens(peer_cip, peer)
        used_fallback_s = not cities_s
        used_fallback_p = not cities_p
        if not cities_s:
            cities_s = _neighborhood_fallback_tokens(subject_cip)
        if not cities_p:
            cities_p = _neighborhood_fallback_tokens(peer_cip)

        city_j = _jaccard(cities_s, cities_p)
        raw_before_bonus = 100.0 * city_j
        s_city = _resolve_primary_city(subject, subject_cip)
        p_city = _resolve_primary_city(peer, peer_cip)
        same_primary = bool(s_city and p_city and s_city == p_city)

        raw, detail = geographic_overlap_raw(subject_cip, peer_cip, subject, peer)
        score, breakdown = weighted_fit(
            [("geographic_overlap", "Geographic overlap", int(round(raw)), 25, detail)]
        )
        geo_pts = breakdown[0].points if breakdown else 0

        print("=== Geo overlap diagnostic: 1921 (LMDG) vs 670 (Fusion) ===")
        print(f"subject.primary_city: {subject.primary_city!r}")
        print(f"peer.primary_city:    {peer.primary_city!r}")
        print(f"cities_S ({len(cities_s)}): {sorted(cities_s)}")
        print(f"cities_P ({len(cities_p)}): {sorted(cities_p)}")
        print(f"used_neighborhood_fallback_S: {used_fallback_s}")
        print(f"used_neighborhood_fallback_P: {used_fallback_p}")
        print(f"city_jaccard: {city_j:.4f}")
        print(f"raw before primary-city bonus: {raw_before_bonus:.2f}")
        print(f"resolved primary_city S: {s_city!r}")
        print(f"resolved primary_city P: {p_city!r}")
        print(f"same_primary_city: {same_primary}")
        print(f"+15 bonus would apply: {same_primary}")
        print(f"50/100 floor would apply: {same_primary}")
        print(f"final raw_geo: {raw:.2f}")
        print(f"threat geo points (/25): {geo_pts}")
        print(f"detail: {detail}")
        print(f"subject CIP service_cities: {subject_cip.service_cities[:8]}")
        print(f"subject CIP neighborhoods sample: {subject_cip.neighborhoods[:8]}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
