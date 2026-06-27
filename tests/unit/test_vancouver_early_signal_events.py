"""Unit tests for Vancouver early signal event classification."""

from __future__ import annotations

from scraper.vancouver_early_signal_events import (
    classify_city_project,
    extract_project_id,
)


def test_extract_project_id_from_url():
    url = (
        "http://vancouver.ca/communitypages_wa/index.cfm"
        "?fuseaction=PRJ.projectdetails&selProjectID=10716"
    )
    assert extract_project_id(url) == "10716"


def test_classify_rezoning_application():
    assert classify_city_project("CD-1 Text Amendment") == "rezoning_application"
    assert classify_city_project("Rezoning - Increased Office Use") == "rezoning_application"


def test_classify_development_permit_application():
    assert classify_city_project("Mixed-use Building") == "development_permit_application"
    assert classify_city_project("Multiple Dwelling Building") == "development_permit_application"
    assert classify_city_project("One-Family Dwelling") == "development_permit_application"


def test_classify_skips_minor_alterations():
    assert classify_city_project("Interior and Exterior Alterations") is None
    assert classify_city_project("Parking Lot") is None
