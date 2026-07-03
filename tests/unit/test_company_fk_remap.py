"""Unit tests for FK remap during canonical merge."""

from __future__ import annotations

from pipeline.company_fk_remap import FK_REMAP_SPECS, remap_company_foreign_keys


def test_fk_remap_specs_include_required_tables():
    tables = {spec.table for spec in FK_REMAP_SPECS}
    assert "contract_awards" in tables
    assert "tender_outcomes" in tables
    assert "client_profiles" in tables
    assert "company_wiki" in tables
    assert "google_enrichment_logs" in tables
    assert "permits" in tables
    assert "tender_matches" not in tables


def test_contract_awards_fk_column_name():
    awards = next(spec for spec in FK_REMAP_SPECS if spec.table == "contract_awards")
    assert awards.column == "company_id"


def test_remap_empty_map_is_noop():
    class _SessionStub:
        def execute(self, *_a, **_k):
            raise AssertionError("no SQL expected")

    summary = remap_company_foreign_keys(_SessionStub(), {})
    assert summary["updated"] == 0
