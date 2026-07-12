"""Phase 1 CI regression contracts — lock durable engineering invariants.

Does not change business logic. Fails CI if protected contracts drift.
"""

from __future__ import annotations

from db.db_safety import is_production_database_url, is_production_host
from pipeline.cip_schema import CompanyIntelligenceProfile
from pipeline.unified_opportunities import get_unified_opportunities
from tests.unit.competitive_fixtures import make_cip


def test_production_hosts_still_detected() -> None:
    assert is_production_host("acela.proxy.rlwy.net") is True
    assert is_production_host("postgres.railway.internal") is True
    assert is_production_host("localhost") is False


def test_production_database_url_refusal_contract() -> None:
    assert is_production_database_url(
        "postgresql://u:p@acela.proxy.rlwy.net:47306/railway"
    )
    assert not is_production_database_url("postgresql://u:p@localhost:5432/bc_tenders")


def test_cip_schema_requires_sector_confidence() -> None:
    cip = make_cip(company_id=8638)
    assert cip.company_id == 8638
    assert isinstance(cip.sector_confidence, str)
    assert cip.sector_confidence != ""


def test_cip_to_dict_preserves_company_id() -> None:
    cip = make_cip(company_id=8638, name="Pontem Group")
    payload = cip.to_dict() if hasattr(cip, "to_dict") else None
    if payload is None:
        # dataclass-style fallback
        payload = {
            "company_id": cip.company_id,
            "sector_confidence": cip.sector_confidence,
        }
    assert payload["company_id"] == 8638
    assert "sector_confidence" in payload


def test_unified_opportunities_callable_contract() -> None:
    """Unified feed entrypoint must remain importable (dual-score surface)."""
    assert callable(get_unified_opportunities)


def test_company_intelligence_profile_fields_stable() -> None:
    fields = getattr(CompanyIntelligenceProfile, "__dataclass_fields__", {})
    required = {
        "company_id",
        "kind",
        "name",
        "dominant_sector",
        "sector_confidence",
        "sector_focus",
    }
    assert required.issubset(set(fields.keys()))


def test_quality_gate_workflow_allows_skipped_opencode_review() -> None:
    """OpenCode runs only on pull_request; push/workflow_dispatch must not fail the gate."""
    from pathlib import Path

    workflow = Path(".github/workflows/quality-gate.yml").read_text(encoding="utf-8")
    assert "if: github.event_name == 'pull_request'" in workflow
    assert (
        'if [ "$job" = "opencode-review" ] && [ "$result" = "skipped" ]; then'
        in workflow
    )
    assert (
        "needs.quality-gate.result == 'success'" in workflow
    ), "deploy must be explicitly gated on a green Quality Gate"
