"""Unit tests for KG validation snapshot."""

from __future__ import annotations

from unittest.mock import MagicMock

from pipeline.kg_validation_snapshot import collect_kg_validation_snapshot


def test_collect_snapshot_returns_flags(monkeypatch):
    monkeypatch.delenv("KG_OBSERVATION_DUAL_WRITE", raising=False)
    monkeypatch.delenv("KG_GATEWAY_SHADOW", raising=False)
    monkeypatch.delenv("KG_GATEWAY_ENFORCE", raising=False)

    session = MagicMock()
    session.execute.return_value.all.return_value = []
    session.scalars.return_value.all.return_value = []
    # outbox_pending, outbox_total, permits_total, recent_permit_run, recent_award_run,
    # kg_observations exists, kg_outbox_events exists, kg_engine_decision_records exists
    session.scalar.side_effect = [0, 0, 0, None, None, True, True, True]

    snapshot = collect_kg_validation_snapshot(session)
    assert "flags" in snapshot
    assert snapshot["flags"]["KG_OBSERVATION_DUAL_WRITE"] is False
    assert "observations" in snapshot
    assert "decisions" in snapshot
