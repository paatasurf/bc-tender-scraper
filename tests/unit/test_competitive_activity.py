"""Unit tests for competitive activity helpers."""

from __future__ import annotations

from datetime import date, timedelta

from pipeline.competitive_intel.activity import (
    buyer_overlap_bonus,
    cohort_p90,
    recency_score,
)


def test_recency_score_recent():
    recent = (date.today() - timedelta(days=30)).isoformat()
    assert recency_score(recent) > 90


def test_recency_score_old():
    old = (date.today() - timedelta(days=400)).isoformat()
    assert recency_score(old) < 10


def test_cohort_p90():
    assert cohort_p90([1, 2, 3, 10, 20]) >= 10


def test_buyer_overlap_bonus():
    bonus = buyer_overlap_bonus(["City of Vancouver"], ["city of vancouver", "Other"])
    assert bonus > 0
