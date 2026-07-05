"""Unit tests for pipeline.identity_parser."""

from __future__ import annotations

import pytest

from pipeline.identity_parser import (
    PARSER_VERSION,
    ParsedIdentity,
    RelationshipType,
    parse_identity,
)


@pytest.mark.parametrize(
    ("raw", "person", "business", "rel"),
    [
        ("David Wong DBA: WHG Design", "David Wong", "WHG Design", RelationshipType.DBA),
        ("Harbhajan Karra DBA: Grand Van Homes Inc", "Harbhajan Karra", "Grand Van Homes Inc", RelationshipType.DBA),
        ("Andy Koppang DBA: Syncor Solutions Limited", "Andy Koppang", "Syncor Solutions Limited", RelationshipType.DBA),
        ("Chris Burrows DBA: Ledcor", "Chris Burrows", "Ledcor", RelationshipType.DBA),
        ("Alex Olaru DBA: WSP Canada Inc.", "Alex Olaru", "WSP Canada Inc.", RelationshipType.DBA),
        ("Jack Hui DBA: Pontem Group", "Jack Hui", "Pontem Group", RelationshipType.DBA),
    ],
)
def test_dba_colon_patterns(raw: str, person: str, business: str, rel: RelationshipType) -> None:
    parsed = parse_identity(raw)
    assert parsed.person_name == person
    assert parsed.business_name == business
    assert parsed.relationship_type == rel
    assert parsed.parse_confidence == 1.0
    assert parsed.resolution_target() == business


def test_dba_space_pattern() -> None:
    parsed = parse_identity("Jane Doe DBA Acme Builders Ltd")
    assert parsed.person_name == "Jane Doe"
    assert parsed.business_name == "Acme Builders Ltd"
    assert parsed.relationship_type == RelationshipType.DBA


def test_doing_business_as_pattern() -> None:
    parsed = parse_identity("Sam Lee Doing Business As Lee Construction")
    assert parsed.person_name == "Sam Lee"
    assert parsed.business_name == "Lee Construction"
    assert parsed.relationship_type == RelationshipType.DBA


def test_operating_as_patterns() -> None:
    oa = parse_identity("Pat Smith O/A Smith Design")
    assert oa.relationship_type == RelationshipType.OPERATING_AS
    assert oa.person_name == "Pat Smith"
    assert oa.business_name == "Smith Design"

    operating = parse_identity("Pat Smith Operating As Smith Design")
    assert operating.relationship_type == RelationshipType.OPERATING_AS


def test_care_of_patterns() -> None:
    parsed = parse_identity("John Agent c/o ABC Construction Ltd")
    assert parsed.relationship_type == RelationshipType.CARE_OF
    assert parsed.person_name == "John Agent"
    assert parsed.business_name == "ABC Construction Ltd"
    assert parsed.resolution_target() == "ABC Construction Ltd"


def test_joint_venture_pattern() -> None:
    parsed = parse_identity("Acme Corp Joint Venture Beta Builders Inc")
    assert parsed.relationship_type == RelationshipType.JOINT_VENTURE
    assert parsed.business_name == "Beta Builders Inc"


def test_partnership_ampersand() -> None:
    parsed = parse_identity("Company A & Company B")
    assert parsed.relationship_type == RelationshipType.PARTNERSHIP
    assert parsed.business_name == "Company B"
    assert parsed.secondary_business_name == "Company A"


def test_slash_trade_name_person() -> None:
    parsed = parse_identity("David Evans / WSP Canada Inc")
    assert parsed.relationship_type == RelationshipType.TRADE_NAME
    assert parsed.person_name == "David Evans"
    assert parsed.business_name == "WSP Canada Inc"


def test_plain_person() -> None:
    for name in ("Akash Sidhu", "Naki Ocran", "Kevin To", "Michael Yee"):
        parsed = parse_identity(name)
        assert parsed.relationship_type == RelationshipType.PLAIN_PERSON
        assert parsed.person_name == name
        assert parsed.business_name is None
        assert parsed.resolution_target() is None


def test_plain_company() -> None:
    parsed = parse_identity("Pontem Group Inc.")
    assert parsed.relationship_type == RelationshipType.PLAIN_COMPANY
    assert parsed.person_name is None
    assert parsed.business_name == "Pontem Group Inc."
    assert parsed.resolution_target() == "Pontem Group Inc."


def test_empty_input() -> None:
    parsed = parse_identity("")
    assert parsed.relationship_type == RelationshipType.UNPARSEABLE
    assert parsed.parse_confidence == 0.0


def test_deterministic() -> None:
    raw = "David Wong DBA: WHG Design"
    assert parse_identity(raw) == parse_identity(raw)


def test_parser_version() -> None:
    assert parse_identity("Test Co Ltd").parser_version == PARSER_VERSION


def test_to_dict() -> None:
    parsed = parse_identity("David Wong DBA: WHG Design")
    d = parsed.to_dict()
    assert d["raw_identity"] == "David Wong DBA: WHG Design"
    assert d["business_name"] == "WHG Design"
    assert d["relationship_type"] == "dba"


def test_raw_identity_preserved_unchanged() -> None:
    raw = "  David Wong DBA: WHG Design  "
    parsed = parse_identity(raw)
    assert parsed.raw_identity == "David Wong DBA: WHG Design"
