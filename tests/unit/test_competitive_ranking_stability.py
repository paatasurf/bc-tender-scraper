"""Regression tests for deterministic Market ranking under score ties."""

from __future__ import annotations

from pipeline.competitive_intel.peers import rank_by_similarity
from tests.unit.competitive_fixtures import make_cip, make_company


def test_rank_by_similarity_breaks_equal_scores_by_company_id():
    subject = make_company(id=1)
    subject_cip = make_cip(company_id=1)
    higher_id = make_company(id=30, name="Higher ID")
    lower_id = make_company(id=20, name="Lower ID")
    peer_cips = {
        20: make_cip(company_id=20),
        30: make_cip(company_id=30),
    }

    first = rank_by_similarity(
        [higher_id, lower_id],
        subject=subject,
        subject_cip=subject_cip,
        peer_cips=peer_cips,
    )
    second = rank_by_similarity(
        [lower_id, higher_id],
        subject=subject,
        subject_cip=subject_cip,
        peer_cips=peer_cips,
    )

    assert [peer.company_id for peer in first] == [20, 30]
    assert [peer.company_id for peer in second] == [20, 30]
