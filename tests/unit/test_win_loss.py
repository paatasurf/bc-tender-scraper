"""Unit tests for win/loss outcome tracking (Phase X.1.5)."""

from __future__ import annotations

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from api.win_loss import (
    RecordOutcomeRequest,
    compute_win_loss_stats,
    outcome_stats,
    upsert_outcome,
)
from db.models import Base, TenderOutcome


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[TenderOutcome.__table__])
    return sessionmaker(bind=engine)()


def test_record_win_produces_correct_win_rate() -> None:
    session = _session()
    try:
        upsert_outcome(
            session,
            RecordOutcomeRequest(
                company_id=42,
                tender_id="t-100",
                tender_title="Civic Centre",
                outcome="won",
                bid_amount=1_000_000,
                award_amount=950_000,
            ),
        )
        upsert_outcome(
            session,
            RecordOutcomeRequest(
                company_id=42,
                tender_id="t-200",
                tender_title="School Board",
                outcome="lost",
                bid_amount=500_000,
            ),
        )
        session.commit()

        rows = session.scalars(
            select(TenderOutcome).where(TenderOutcome.company_id == 42)
        ).all()
        stats = compute_win_loss_stats(list(rows))

        assert stats["total_bids"] == 2
        assert stats["won"] == 1
        assert stats["lost"] == 1
        assert stats["win_rate"] == 0.5
        assert stats["avg_bid_amount"] == 750_000.0
        assert stats["total_won_value"] == 950_000.0
    finally:
        session.close()


def test_upsert_does_not_duplicate_rows() -> None:
    session = _session()
    try:
        payload = RecordOutcomeRequest(
            company_id=7,
            tender_id="dup-tender",
            tender_title="First title",
            outcome="pending",
            bid_amount=100_000,
        )
        first = upsert_outcome(session, payload)
        session.commit()

        payload = RecordOutcomeRequest(
            company_id=7,
            tender_id="dup-tender",
            tender_title="Updated title",
            outcome="won",
            bid_amount=120_000,
            award_amount=115_000,
        )
        second = upsert_outcome(session, payload)
        session.commit()

        rows = session.scalars(
            select(TenderOutcome).where(TenderOutcome.company_id == 7)
        ).all()

        assert len(rows) == 1
        assert first.id == second.id
        assert rows[0].tender_title == "Updated title"
        assert rows[0].outcome == "won"
        assert float(rows[0].award_amount) == 115_000.0
    finally:
        session.close()


def test_empty_history_returns_zeros_not_errors() -> None:
    stats = compute_win_loss_stats([])
    assert stats == {
        "total_bids": 0,
        "won": 0,
        "lost": 0,
        "withdrew": 0,
        "pending": 0,
        "win_rate": 0.0,
        "avg_bid_amount": 0.0,
        "total_won_value": 0.0,
    }


def test_outcome_stats_route_empty_company(monkeypatch) -> None:
    session = _session()

    def _get_session():
        return session

    monkeypatch.setattr("api.win_loss.get_session", _get_session)

    try:
        payload = outcome_stats(999)
        assert payload["company_id"] == 999
        assert payload["total_bids"] == 0
        assert payload["win_rate"] == 0.0
    finally:
        session.close()
