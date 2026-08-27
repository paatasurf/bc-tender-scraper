"""Unit tests for Vancouver early signal enrichment parsing and reliability."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db.models import Base, EarlySignalEvent
from scraper import vancouver_early_signal_enrichment as enrichment
from scraper.shapeyourcity_development import (
    build_development_application_url,
    extract_address_from_project_name,
    extract_applicant_from_text,
    extract_reference_number,
    extract_reference_number_from_url,
    project_to_enrichment,
    score_project_match,
)


def _session_factory():
    """A sessionmaker bound to a shared in-memory SQLite DB, so multiple
    independent sessions (a read session plus per-chunk write sessions) can
    all see the same data -- mirroring the real short-lived-session
    architecture under test."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[EarlySignalEvent.__table__])
    return sessionmaker(bind=engine)


def _seed(Session, rows: list[dict]) -> None:
    session = Session()
    for row in rows:
        session.add(EarlySignalEvent(**row))
    session.commit()
    session.close()


def test_extract_reference_number_from_project_name():
    name = "3075 Arbutus St and 2115 W 15th Ave (DP-2026-00404) development application"
    assert extract_reference_number(name) == "DP-2026-00404"


def test_build_development_application_url():
    url = build_development_application_url("DP-2026-00404")
    assert url.endswith("development-applications.aspx?RN=DP-2026-00404")


def test_extract_reference_number_from_url():
    url = "https://vancouver.ca/home-property-development/development-applications.aspx?RN=DP-2022-00841"
    assert extract_reference_number_from_url(url) == "DP-2022-00841"


def test_extract_address_from_project_name():
    name = "3226 W 51st Ave (DP-2022-00841) development application"
    assert extract_address_from_project_name(name) == "3226 W 51st Ave"


def test_extract_applicant_from_description_html():
    html = (
        "<p>Thinkspace Architecture Planning Interior Design has applied to the City of "
        "Vancouver to develop a new two-storey Child Day Care Facility.</p>"
    )
    assert (
        extract_applicant_from_text(html)
        == "Thinkspace Architecture Planning Interior Design"
    )


def test_project_to_enrichment():
    project = {
        "name": "3226 W 51st Ave (DP-2022-00841) development application",
        "permalink": "3226-w-51st-ave",
        "description": (
            "<p>Example Development Ltd has applied to the City of Vancouver "
            "for a new one-family dwelling valued at $2,500,000.</p>"
        ),
        "projectTagList": ["Development", "Kerrisdale"],
    }
    payload = project_to_enrichment(project)
    assert payload["address"] == "3226 W 51st Ave"
    assert payload["applicant"] == "Example Development Ltd"
    assert payload["project_value"] == "$2,500,000"
    assert "RN=DP-2022-00841" in payload["url_link"]


def test_score_project_match_prefers_region_and_type():
    project = {
        "name": "1343 E 14th Ave (DP-2023-00256) development application",
        "description": "One-family dwelling proposal",
        "projectTagList": ["Development", "Kensington-Cedar Cottage"],
    }
    score = score_project_match(
        region="Kensington-Cedar Cottage",
        property_type="One-Family Dwelling",
        project=project,
    )
    assert score >= 15


def test_fetch_detail_fields_logs_and_recovers_on_detail_page_error(
    monkeypatch, capsys
):
    def _boom(session, url):
        raise RuntimeError("boom")

    monkeypatch.setattr(enrichment, "fetch_html", _boom)

    url = "https://vancouver.ca/home-property-development/development-applications.aspx?RN=DP-1"
    detail, had_error = enrichment._fetch_detail_fields(None, {}, url)

    assert detail == {}
    assert had_error is True
    out = capsys.readouterr().out
    assert "[Vancouver Enrichment] Detail page fetch failed" in out
    assert "boom" in out


def test_fetch_detail_fields_logs_and_recovers_on_shapeyourcity_error(
    monkeypatch, capsys
):
    def _boom(session, url):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(enrichment, "fetch_html", _boom)

    detail, had_error = enrichment._fetch_detail_fields(
        None, {"permalink": "3226-w-51st-ave"}, ""
    )

    assert detail == {}
    assert had_error is True
    out = capsys.readouterr().out
    assert "[Vancouver Enrichment] ShapeYourCity fetch failed" in out
    assert "kaboom" in out


# --- Reliability architecture: session lifecycle, chunked commits, idempotency ---


def test_read_session_is_closed_before_external_fetch_begins(monkeypatch):
    Session = _session_factory()
    _seed(
        Session,
        [{"external_id": "e1", "url_link": "", "region": "", "property_type": ""}],
    )

    read_session = Session()
    order: list[str] = []
    real_close = read_session.close
    monkeypatch.setattr(
        read_session,
        "close",
        lambda: (order.append("read_session_closed"), real_close()),
    )
    monkeypatch.setattr(
        enrichment,
        "create_session",
        lambda: (order.append("http_session_created"), MagicMock())[1],
    )
    monkeypatch.setattr(enrichment, "load_development_projects", lambda *, session: [])

    result = enrichment.enrich_early_signal_events(
        read_session, get_session=Session, persist=False
    )

    assert order == ["read_session_closed", "http_session_created"]
    assert result["candidates"] == 1


def test_multiple_chunks_commit_successfully(monkeypatch):
    Session = _session_factory()
    _seed(
        Session,
        [
            {"external_id": f"e{i}", "url_link": "", "region": "", "property_type": ""}
            for i in range(7)
        ],
    )

    monkeypatch.setattr(enrichment, "create_session", lambda: MagicMock())
    monkeypatch.setattr(
        enrichment, "load_development_projects", lambda *, session: [{"dummy": True}]
    )
    monkeypatch.setattr(
        enrichment,
        "enrich_early_signal_event",
        lambda candidate, projects, *, http_session, fetch_details: (
            {
                "address": f"Addr {candidate.id}",
                "applicant": "Acme",
                "project_value": "$1",
            },
            False,
        ),
    )

    read_session = Session()
    result = enrichment.enrich_early_signal_events(
        read_session, get_session=Session, chunk_size=3, persist=True
    )

    assert result["candidates"] == 7
    assert result["fetched"] == 7
    assert result["enriched"] == 7
    assert result["committed_chunks"] == 3  # 3 + 3 + 1
    assert result["write_failures"] == 0

    verify = Session()
    rows = verify.scalars(select(EarlySignalEvent)).all()
    verify.close()
    assert all(row.address.startswith("Addr") for row in rows)


def test_one_external_failure_does_not_cancel_the_batch(monkeypatch):
    Session = _session_factory()
    _seed(
        Session,
        [
            {"external_id": f"e{i}", "url_link": "", "region": "", "property_type": ""}
            for i in range(3)
        ],
    )

    monkeypatch.setattr(enrichment, "create_session", lambda: MagicMock())
    monkeypatch.setattr(
        enrichment, "load_development_projects", lambda *, session: [{"dummy": True}]
    )

    def _fake_enrich(candidate, projects, *, http_session, fetch_details):
        had_error = candidate.external_id == "e1"
        payload = {
            "address": f"Addr {candidate.id}",
            "applicant": "Acme",
            "project_value": "$1",
        }
        return payload, had_error

    monkeypatch.setattr(enrichment, "enrich_early_signal_event", _fake_enrich)

    read_session = Session()
    result = enrichment.enrich_early_signal_events(
        read_session, get_session=Session, persist=True
    )

    assert result["candidates"] == 3
    assert result["external_failures"] == 1
    assert result["fetched"] == 3
    assert result["enriched"] == 3
    assert result["skipped"] == 0


def test_write_failure_in_one_chunk_preserves_earlier_committed_chunks(monkeypatch):
    Session = _session_factory()
    _seed(
        Session,
        [
            {"external_id": f"e{i}", "url_link": "", "region": "", "property_type": ""}
            for i in range(6)
        ],
    )

    monkeypatch.setattr(enrichment, "create_session", lambda: MagicMock())
    monkeypatch.setattr(
        enrichment, "load_development_projects", lambda *, session: [{"dummy": True}]
    )
    monkeypatch.setattr(
        enrichment,
        "enrich_early_signal_event",
        lambda candidate, projects, *, http_session, fetch_details: (
            {
                "address": f"Addr {candidate.id}",
                "applicant": "Acme",
                "project_value": "$1",
            },
            False,
        ),
    )

    call_count = {"n": 0}

    def _flaky_get_session():
        call_count["n"] += 1
        session = Session()
        if call_count["n"] == 2:

            def _boom():
                raise RuntimeError("simulated write failure")

            session.commit = _boom
        return session

    read_session = Session()
    result = enrichment.enrich_early_signal_events(
        read_session, get_session=_flaky_get_session, chunk_size=2, persist=True
    )

    assert result["candidates"] == 6
    assert (
        result["committed_chunks"] == 2
    )  # chunk 1 (e0,e1) and chunk 3 (e4,e5) succeed
    assert result["write_failures"] == 1  # chunk 2 (e2,e3) fails

    verify = Session()
    rows = {
        row.external_id: row.address
        for row in verify.scalars(select(EarlySignalEvent)).all()
    }
    verify.close()

    assert rows["e0"] != ""
    assert rows["e1"] != ""
    assert rows["e2"] == ""
    assert rows["e3"] == ""
    assert rows["e4"] != ""
    assert rows["e5"] != ""


def test_repeat_run_does_not_erase_existing_enrichment(monkeypatch):
    Session = _session_factory()
    _seed(
        Session,
        [
            {
                "external_id": "e1",
                "url_link": "",
                "region": "",
                "property_type": "",
                "address": "123 Real St",
                "applicant": "Existing Applicant",
                "project_value": "$999",
            }
        ],
    )

    monkeypatch.setattr(enrichment, "create_session", lambda: MagicMock())
    monkeypatch.setattr(
        enrichment, "load_development_projects", lambda *, session: [{"dummy": True}]
    )
    monkeypatch.setattr(
        enrichment,
        "enrich_early_signal_event",
        lambda candidate, projects, *, http_session, fetch_details: (
            {"address": "", "applicant": "", "project_value": ""},
            False,
        ),
    )

    read_session = Session()
    result = enrichment.enrich_early_signal_events(
        read_session, get_session=Session, force=True, persist=True
    )

    assert result["enriched"] == 0
    assert result["no_new_values"] == 1

    verify = Session()
    row = verify.scalars(select(EarlySignalEvent)).first()
    verify.close()

    assert row.address == "123 Real St"
    assert row.applicant == "Existing Applicant"
    assert row.project_value == "$999"


def test_payload_repeating_existing_values_is_not_counted_as_enriched(monkeypatch):
    Session = _session_factory()
    _seed(
        Session,
        [
            {
                "external_id": "e1",
                "url_link": "",
                "region": "",
                "property_type": "",
                "address": "123 Real St",
                "applicant": "Existing Applicant",
                "project_value": "$999",
            }
        ],
    )

    monkeypatch.setattr(enrichment, "create_session", lambda: MagicMock())
    monkeypatch.setattr(
        enrichment, "load_development_projects", lambda *, session: [{"dummy": True}]
    )
    monkeypatch.setattr(
        enrichment,
        "enrich_early_signal_event",
        lambda candidate, projects, *, http_session, fetch_details: (
            {
                "address": "123 Real St",
                "applicant": "Existing Applicant",
                "project_value": "$999",
            },
            False,
        ),
    )

    read_session = Session()
    result = enrichment.enrich_early_signal_events(
        read_session, get_session=Session, force=True, persist=True
    )

    assert result["fetched"] == 1
    assert result["enriched"] == 0
    assert result["no_new_values"] == 1
    assert result["committed_chunks"] == 1
    assert result["results"][0]["status"] == "no_new_values"

    verify = Session()
    row = verify.scalars(select(EarlySignalEvent)).first()
    verify.close()

    assert row.address == "123 Real St"
    assert row.applicant == "Existing Applicant"
    assert row.project_value == "$999"


def test_run_returns_honest_counts_and_never_raises(monkeypatch):
    Session = _session_factory()
    _seed(
        Session,
        [
            {"external_id": "e1", "url_link": "", "region": "", "property_type": ""},
            {"external_id": "e2", "url_link": "", "region": "", "property_type": ""},
        ],
    )

    monkeypatch.setattr(enrichment, "create_session", lambda: MagicMock())
    monkeypatch.setattr(
        enrichment, "load_development_projects", lambda *, session: [{"dummy": True}]
    )

    def _fake_enrich(candidate, projects, *, http_session, fetch_details):
        if candidate.external_id == "e1":
            return None, True  # no project match, and an external fetch error occurred
        return {
            "address": "1 Main St",
            "applicant": "Acme",
            "project_value": "$1",
        }, False

    monkeypatch.setattr(enrichment, "enrich_early_signal_event", _fake_enrich)

    read_session = Session()
    result = enrichment.enrich_early_signal_events(
        read_session, get_session=Session, persist=True
    )

    required_keys = {
        "candidates",
        "fetched",
        "enriched",
        "skipped",
        "external_failures",
        "write_failures",
        "committed_chunks",
    }
    assert required_keys.issubset(result.keys())
    assert result["candidates"] == 2
    assert result["fetched"] == 1
    assert result["skipped"] == 1
    assert result["external_failures"] == 1
    assert result["enriched"] == 1
    assert result["committed_chunks"] == 1
    assert result["write_failures"] == 0


# --- HTTP resource lifecycle ---


def test_http_session_is_closed_after_normal_run(monkeypatch):
    Session = _session_factory()
    _seed(
        Session,
        [{"external_id": "e1", "url_link": "", "region": "", "property_type": ""}],
    )

    fake_http = MagicMock()
    monkeypatch.setattr(enrichment, "create_session", lambda: fake_http)
    monkeypatch.setattr(enrichment, "load_development_projects", lambda *, session: [])

    read_session = Session()
    enrichment.enrich_early_signal_events(
        read_session, get_session=Session, persist=False
    )

    fake_http.close.assert_called_once()


def test_http_session_is_closed_when_load_development_projects_raises(monkeypatch):
    Session = _session_factory()
    _seed(
        Session,
        [{"external_id": "e1", "url_link": "", "region": "", "property_type": ""}],
    )

    fake_http = MagicMock()
    monkeypatch.setattr(enrichment, "create_session", lambda: fake_http)

    def _boom(*, session):
        raise RuntimeError("index page unreachable")

    monkeypatch.setattr(enrichment, "load_development_projects", _boom)

    read_session = Session()
    with pytest.raises(RuntimeError, match="index page unreachable"):
        enrichment.enrich_early_signal_events(
            read_session, get_session=Session, persist=False
        )

    fake_http.close.assert_called_once()
