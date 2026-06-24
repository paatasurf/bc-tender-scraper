"""Regression tests for Surrey permits scrape API defaults."""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.main import app
from scraper.surrey_permits import DEFAULT_INCREMENTAL_DAYS


def test_surrey_scrape_api_defaults_to_weekly_incremental(monkeypatch):
    calls: list[dict[str, object]] = []

    def fake_scrape_surrey_permits(*, days: int | None, persist: bool):
        calls.append({"days": days, "persist": persist})
        return {"status": "ok"}

    monkeypatch.setattr(
        "scraper.surrey_permits.scrape_surrey_permits",
        fake_scrape_surrey_permits,
    )

    response = TestClient(app).get("/api/scrape/surrey-permits")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert calls == [{"days": DEFAULT_INCREMENTAL_DAYS, "persist": True}]


def test_surrey_scrape_api_honors_explicit_incremental_days(monkeypatch):
    calls: list[dict[str, object]] = []

    def fake_scrape_surrey_permits(*, days: int | None, persist: bool):
        calls.append({"days": days, "persist": persist})
        return {"status": "ok"}

    monkeypatch.setattr(
        "scraper.surrey_permits.scrape_surrey_permits",
        fake_scrape_surrey_permits,
    )

    response = TestClient(app).get("/api/scrape/surrey-permits?days=14")

    assert response.status_code == 200
    assert calls == [{"days": 14, "persist": True}]


def test_surrey_scrape_api_allows_explicit_full_history(monkeypatch):
    calls: list[dict[str, object]] = []

    def fake_scrape_surrey_permits(*, days: int | None, persist: bool):
        calls.append({"days": days, "persist": persist})
        return {"status": "ok"}

    monkeypatch.setattr(
        "scraper.surrey_permits.scrape_surrey_permits",
        fake_scrape_surrey_permits,
    )

    response = TestClient(app).get("/api/scrape/surrey-permits?full=true")

    assert response.status_code == 200
    assert calls == [{"days": None, "persist": True}]
