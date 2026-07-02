from __future__ import annotations

import pytest

from scraper.tender_category import (
    CONSTRUCTION,
    SERVICES,
    CANADABUYS_SOURCE,
    MERX_SOURCE,
    resolve_tender_category,
)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        (
            "EZ899-261706 - Transportation Engineering and Prime Consultant Services SOA",
            SERVICES,
        ),
        (
            "2026-011 ARCHITECTURAL SERVICES FOR CARIHI SECONDARY SCHOOL REBUILD",
            SERVICES,
        ),
        (
            "RFPQ-27-0042 Consulting Services for Campus Wayfinding Program",
            SERVICES,
        ),
        (
            "FH-HAZMAT-2026-RFP - Request for Proposal for Hazardous Materials Consulting and Testing Services",
            SERVICES,
        ),
        (
            "Quick Response Source List for Surveying and Geomatics Services",
            SERVICES,
        ),
        (
            "Construction Management Consulting Services for Northwest Langley Wastewater Treatment Plant Expansion Project",
            SERVICES,
        ),
        (
            "RFP for Construction Management Services for SFU Lorne Davies Complex Facade",
            CONSTRUCTION,
        ),
        (
            "Domestic Hot Water Tank and Boiler Replacement Design-Build",
            CONSTRUCTION,
        ),
        (
            "ITQ No. 0618HA-2026 Janitorial Services - Port Hardy",
            CONSTRUCTION,
        ),
        (
            "26-1954-RFP Piping Installation Services",
            CONSTRUCTION,
        ),
        (
            "376541 RFP Painting and Graffiti Removal Services",
            CONSTRUCTION,
        ),
        (
            "RFP 2026-531 - Restoration Services",
            CONSTRUCTION,
        ),
        (
            "Professional Services Request for Building Maintenance Services",
            CONSTRUCTION,
        ),
    ],
)
def test_merx_title_classification(title: str, expected: str) -> None:
    assert (
        resolve_tender_category(title=title, source=MERX_SOURCE)
        == expected
    )


def test_canadabuys_trusts_listing_category() -> None:
    assert (
        resolve_tender_category(
            title="Bridge Engineering Services",
            source=CANADABUYS_SOURCE,
            raw_category="Services",
        )
        == SERVICES
    )
    assert (
        resolve_tender_category(
            title="Bridge Engineering Services",
            source=CANADABUYS_SOURCE,
            raw_category="Construction",
        )
        == CONSTRUCTION
    )


def test_unknown_merx_title_defaults_to_construction() -> None:
    assert (
        resolve_tender_category(
            title="Phase 2 EZ899-270168 Penticton Airport Omni-Direct",
            source=MERX_SOURCE,
        )
        == CONSTRUCTION
    )
