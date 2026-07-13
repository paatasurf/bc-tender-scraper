"""Unit tests for Vancouver early signal enrichment parsing."""

from __future__ import annotations

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
    detail = enrichment._fetch_detail_fields(None, {}, url)

    assert detail == {}
    out = capsys.readouterr().out
    assert "[Vancouver Enrichment] Detail page fetch failed" in out
    assert "boom" in out


def test_fetch_detail_fields_logs_and_recovers_on_shapeyourcity_error(
    monkeypatch, capsys
):
    def _boom(session, url):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(enrichment, "fetch_html", _boom)

    detail = enrichment._fetch_detail_fields(None, {"permalink": "3226-w-51st-ave"}, "")

    assert detail == {}
    out = capsys.readouterr().out
    assert "[Vancouver Enrichment] ShapeYourCity fetch failed" in out
    assert "kaboom" in out
