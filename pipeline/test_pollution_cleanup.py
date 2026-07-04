"""Class D cleanup for Jul 3 test pollution (companies 572934, 572936–572950)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

TEST_POLLUTION_COMPANY_IDS: tuple[int, ...] = (572934,) + tuple(range(572936, 572951))
TEST_POLLUTION_REGISTRY_LINK_ID = 1
TEST_POLLUTION_ODBUS_IDX = "t1-1"

_FK_CHECKS: tuple[tuple[str, str], ...] = (
    ("permits.company_id", "SELECT COUNT(*) FROM permits WHERE company_id = ANY(:ids)"),
    ("contract_awards.company_id", "SELECT COUNT(*) FROM contract_awards WHERE company_id = ANY(:ids)"),
    ("tender_outcomes.company_id", "SELECT COUNT(*) FROM tender_outcomes WHERE company_id = ANY(:ids)"),
    ("company_wiki.company_id", "SELECT COUNT(*) FROM company_wiki WHERE company_id = ANY(:ids)"),
    (
        "company_applicant_aliases.canonical_company_id",
        "SELECT COUNT(*) FROM company_applicant_aliases WHERE canonical_company_id = ANY(:ids)",
    ),
    (
        "company_applicant_aliases.alias_company_id",
        "SELECT COUNT(*) FROM company_applicant_aliases WHERE alias_company_id = ANY(:ids)",
    ),
    (
        "companies.canonical_company_id",
        "SELECT COUNT(*) FROM companies WHERE canonical_company_id = ANY(:ids)",
    ),
    ("client_profiles.company_id", "SELECT COUNT(*) FROM client_profiles WHERE company_id = ANY(:ids)"),
    (
        "market_registry.tenderscope_company_id",
        "SELECT COUNT(*) FROM market_registry WHERE tenderscope_company_id = ANY(:ids)",
    ),
    (
        "google_enrichment_logs.company_id",
        "SELECT COUNT(*) FROM google_enrichment_logs WHERE company_id = ANY(:ids)",
    ),
)

# Child rows deleted explicitly before companies — informational only, never block.
_CHILD_ROW_COUNTS: tuple[tuple[str, str], ...] = (
    (
        "company_registry_links.company_id",
        "SELECT COUNT(*) FROM company_registry_links WHERE company_id = ANY(:ids)",
    ),
    (
        "company_score_history.company_id",
        "SELECT COUNT(*) FROM company_score_history WHERE company_id = ANY(:ids)",
    ),
)


@dataclass
class PollutionCleanupPlan:
    company_ids: tuple[int, ...] = TEST_POLLUTION_COMPANY_IDS
    registry_link_id: int = TEST_POLLUTION_REGISTRY_LINK_ID
    odbus_idx: str = TEST_POLLUTION_ODBUS_IDX
    fk_checks: dict[str, int] = field(default_factory=dict)
    child_rows_to_delete: dict[str, int] = field(default_factory=dict)
    companies_found: list[dict[str, Any]] = field(default_factory=list)
    registry_links_found: list[dict[str, Any]] = field(default_factory=list)
    odbus_rows_found: list[dict[str, Any]] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)
    blocked: bool = False

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "operation": "test_pollution_cleanup",
            "class": "D",
            "company_ids": list(self.company_ids),
            "registry_link_id": self.registry_link_id,
            "odbus_idx": self.odbus_idx,
            "fk_checks": self.fk_checks,
            "child_rows_to_delete": self.child_rows_to_delete,
            "companies_found": self.companies_found,
            "registry_links_found": self.registry_links_found,
            "odbus_rows_found": self.odbus_rows_found,
            "validation_errors": self.validation_errors,
            "blocked": self.blocked,
            "planned_deletes": {
                "companies": len(self.companies_found),
                "company_registry_links": len(self.registry_links_found),
                "odbus_reference": len(self.odbus_rows_found),
            },
        }


def build_test_pollution_cleanup_plan(session: Session) -> PollutionCleanupPlan:
    plan = PollutionCleanupPlan()
    params = {"ids": list(plan.company_ids)}

    for label, sql in _FK_CHECKS:
        try:
            plan.fk_checks[label] = int(session.execute(text(sql), params).scalar() or 0)
        except Exception as exc:
            session.rollback()
            plan.fk_checks[label] = -1
            plan.validation_errors.append(f"FK check failed for {label}: {exc}")

    for label, sql in _CHILD_ROW_COUNTS:
        try:
            plan.child_rows_to_delete[label] = int(
                session.execute(text(sql), params).scalar() or 0
            )
        except Exception as exc:
            session.rollback()
            plan.child_rows_to_delete[label] = -1
            plan.validation_errors.append(f"Child row count failed for {label}: {exc}")

    plan.companies_found = [
        dict(row)
        for row in session.execute(
            text(
                """
                SELECT id, name, display_name, entity_role, total_projects, created_at
                FROM companies
                WHERE id = ANY(:ids)
                ORDER BY id
                """
            ),
            params,
        ).mappings()
    ]

    plan.registry_links_found = [
        dict(row)
        for row in session.execute(
            text(
                """
                SELECT id, company_id, source, external_id, linked_at
                FROM company_registry_links
                WHERE id = :link_id OR company_id = ANY(:ids)
                ORDER BY id
                """
            ),
            {"link_id": plan.registry_link_id, "ids": list(plan.company_ids)},
        ).mappings()
    ]

    plan.odbus_rows_found = [
        dict(row)
        for row in session.execute(
            text(
                """
                SELECT odbus_idx, business_name, imported_at
                FROM odbus_reference
                WHERE odbus_idx = :idx
                """
            ),
            {"idx": plan.odbus_idx},
        ).mappings()
    ]

    missing_ids = sorted(set(plan.company_ids) - {row["id"] for row in plan.companies_found})
    if missing_ids:
        plan.validation_errors.append(f"Expected company ids not found (already deleted?): {missing_ids}")

    unexpected_fk = {k: v for k, v in plan.fk_checks.items() if v not in (0, -1)}
    if unexpected_fk:
        plan.blocked = True
        plan.validation_errors.append(f"Non-zero FK references block delete: {unexpected_fk}")

    return plan


def apply_test_pollution_cleanup_plan(session: Session, plan: PollutionCleanupPlan) -> dict[str, Any]:
    if plan.blocked:
        raise ValueError(f"Cleanup blocked: {plan.validation_errors}")

    if plan.validation_errors:
        raise ValueError(f"Cleanup validation failed: {plan.validation_errors}")

    deleted_score_history = session.execute(
        text("DELETE FROM company_score_history WHERE company_id = ANY(:ids)"),
        {"ids": list(plan.company_ids)},
    ).rowcount

    deleted_links = session.execute(
        text(
            """
            DELETE FROM company_registry_links
            WHERE id = :link_id OR company_id = ANY(:ids)
            """
        ),
        {"link_id": plan.registry_link_id, "ids": list(plan.company_ids)},
    ).rowcount

    deleted_odbus = session.execute(
        text("DELETE FROM odbus_reference WHERE odbus_idx = :idx"),
        {"idx": plan.odbus_idx},
    ).rowcount

    deleted_companies = session.execute(
        text("DELETE FROM companies WHERE id = ANY(:ids)"),
        {"ids": list(plan.company_ids)},
    ).rowcount

    session.commit()

    return {
        "status": "applied",
        "deleted_companies": deleted_companies,
        "deleted_registry_links": deleted_links,
        "deleted_score_history": deleted_score_history,
        "deleted_odbus_reference": deleted_odbus,
    }
