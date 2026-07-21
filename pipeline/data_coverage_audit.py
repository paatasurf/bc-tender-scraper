"""Aggregate-only production data coverage audit.

All queries return counts, dates, and fixed categorical dimensions.  No raw
identifier, organization name, address, description, or source payload leaves
the database transaction.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

ARTIFACT_SCHEMA_VERSION = 1
SEVERITIES = ("critical", "high", "medium", "info")


class DataCoverageAuditError(ValueError):
    """Raised when arguments or aggregate contracts are invalid."""


_DATASET_SQL: dict[str, str] = {
    "permits": """
        SELECT COALESCE(NULLIF(BTRIM(p.source), ''), 'unknown') AS source,
               COUNT(*)::bigint AS total,
               COUNT(*) FILTER (WHERE NULLIF(BTRIM(p.external_id), '') IS NULL)::bigint AS missing_source_id,
               COUNT(*) FILTER (WHERE NULLIF(BTRIM(p.applicant), '') IS NULL)::bigint AS missing_applicant,
               COUNT(*) FILTER (WHERE NULLIF(BTRIM(p.address), '') IS NULL)::bigint AS missing_address,
               COUNT(*) FILTER (WHERE NULLIF(BTRIM(p.issue_date), '') IS NULL)::bigint AS missing_date,
               COUNT(*) FILTER (WHERE NULLIF(BTRIM(p.description), '') IS NULL)::bigint AS missing_description,
               COUNT(*) FILTER (WHERE p.company_id IS NOT NULL)::bigint AS linked,
               COUNT(*) FILTER (WHERE p.company_id IS NULL)::bigint AS unlinked,
               COUNT(*) FILTER (WHERE p.company_id IS NOT NULL AND c.id IS NULL)::bigint AS dangling_company_fk,
               MAX(p.scraped_at) AS latest_observed_at
        FROM permits p LEFT JOIN companies c ON c.id = p.company_id
        GROUP BY 1 ORDER BY 1
    """,
    "tenders": """
        SELECT COALESCE(NULLIF(BTRIM(source), ''), 'unknown') AS source,
               COUNT(*)::bigint AS total,
               COUNT(*) FILTER (WHERE NULLIF(BTRIM(tender_id), '') IS NULL)::bigint AS missing_source_id,
               COUNT(*) FILTER (WHERE NULLIF(BTRIM(title), '') IS NULL)::bigint AS missing_title,
               COUNT(*) FILTER (WHERE NULLIF(BTRIM(organization), '') IS NULL)::bigint AS missing_buyer,
               COUNT(*) FILTER (WHERE NULLIF(BTRIM(closing_date), '') IS NULL)::bigint AS missing_closing_date,
               COUNT(*) FILTER (WHERE estimated_value_numeric IS NULL)::bigint AS missing_numeric_value,
               COUNT(*) FILTER (WHERE ai_score IS NULL)::bigint AS unscored,
               COUNT(*) FILTER (WHERE ai_score IS NOT NULL)::bigint AS scored,
               MAX(scraped_at) AS latest_observed_at
        FROM tenders GROUP BY 1 ORDER BY 1
    """,
    "commercial_tenders": """
        SELECT COALESCE(NULLIF(BTRIM(source), ''), 'unknown') AS source,
               COUNT(*)::bigint AS total,
               COUNT(*) FILTER (WHERE NULLIF(BTRIM(tender_id), '') IS NULL)::bigint AS missing_source_id,
               COUNT(*) FILTER (WHERE NULLIF(BTRIM(title), '') IS NULL)::bigint AS missing_title,
               COUNT(*) FILTER (WHERE NULLIF(BTRIM(company), '') IS NULL)::bigint AS missing_buyer,
               COUNT(*) FILTER (WHERE NULLIF(BTRIM(deadline), '') IS NULL)::bigint AS missing_closing_date,
               COUNT(*) FILTER (WHERE estimated_value_numeric IS NULL)::bigint AS missing_numeric_value,
               COUNT(*) FILTER (WHERE ai_score IS NULL)::bigint AS unscored,
               COUNT(*) FILTER (WHERE ai_score IS NOT NULL)::bigint AS scored,
               MAX(scraped_at) AS latest_observed_at
        FROM commercial_tenders GROUP BY 1 ORDER BY 1
    """,
    "contract_awards": """
        SELECT COALESCE(NULLIF(BTRIM(a.source), ''), 'unknown') AS source,
               COUNT(*)::bigint AS total,
               COUNT(*) FILTER (WHERE NULLIF(BTRIM(a.external_id), '') IS NULL)::bigint AS missing_source_id,
               COUNT(*) FILTER (WHERE NULLIF(BTRIM(a.winner_company), '') IS NULL)::bigint AS missing_supplier,
               COUNT(*) FILTER (WHERE NULLIF(BTRIM(a.award_date), '') IS NULL)::bigint AS missing_date,
               COUNT(*) FILTER (WHERE a.award_value IS NULL)::bigint AS missing_value,
               COUNT(*) FILTER (WHERE a.company_id IS NOT NULL)::bigint AS linked,
               COUNT(*) FILTER (WHERE a.company_id IS NULL)::bigint AS unlinked,
               COUNT(*) FILTER (WHERE a.company_id IS NOT NULL AND c.id IS NULL)::bigint AS dangling_company_fk,
               MAX(a.updated_at) AS latest_observed_at
        FROM contract_awards a LEFT JOIN companies c ON c.id = a.company_id
        GROUP BY 1 ORDER BY 1
    """,
    "kg_observations": """
        SELECT COALESCE(NULLIF(BTRIM(source), ''), 'unknown') AS source,
               COUNT(*)::bigint AS total,
               COUNT(*) FILTER (WHERE NULLIF(BTRIM(external_id), '') IS NULL)::bigint AS missing_source_id,
               COUNT(*) FILTER (WHERE NULLIF(BTRIM(content_hash), '') IS NULL)::bigint AS missing_content_hash,
               COUNT(*) FILTER (WHERE status = 'active')::bigint AS active,
               COUNT(*) FILTER (WHERE status = 'quarantined')::bigint AS quarantined,
               MAX(observed_at) AS latest_observed_at
        FROM kg_observations GROUP BY 1 ORDER BY 1
    """,
    "pipeline_runs": """
        SELECT COALESCE(NULLIF(BTRIM(step), ''), 'unknown') AS source,
               COUNT(*)::bigint AS total,
               COUNT(*) FILTER (WHERE status = 'success')::bigint AS succeeded,
               COUNT(*) FILTER (WHERE status = 'failed')::bigint AS failed,
               COUNT(*) FILTER (WHERE status = 'running')::bigint AS running,
               COUNT(*) FILTER (WHERE counts_json IS NULL OR BTRIM(counts_json) IN ('', '{}'))::bigint AS missing_counts,
               MAX(started_at) AS latest_observed_at
        FROM pipeline_runs GROUP BY 1 ORDER BY 1
    """,
}

_GLOBAL_SQL: dict[str, str] = {
    "companies": """
        SELECT COUNT(*)::bigint AS total,
               COUNT(*) FILTER (WHERE NULLIF(BTRIM(company.name), '') IS NULL)::bigint AS missing_name,
               COUNT(*) FILTER (WHERE company.entity_role = 'applicant_alias' AND company.canonical_company_id IS NULL)::bigint AS alias_missing_target,
               COUNT(*) FILTER (WHERE company.entity_role = 'applicant_alias' AND target.entity_role = 'applicant_alias')::bigint AS alias_to_alias,
               COUNT(*) FILTER (WHERE company.canonical_company_id IS NOT NULL AND target.id IS NULL)::bigint AS dangling_canonical_fk,
               COUNT(*) FILTER (WHERE company.lifecycle_status = 'dormant' AND company.is_operating)::bigint AS lifecycle_contradictions,
               COUNT(*) FILTER (WHERE company.lifecycle_status IN ('active', 'quiet') AND NOT company.is_operating)::bigint AS operating_contradictions,
               COUNT(*) FILTER (WHERE company.track_record_score IS NULL AND company.track_record_json IS NULL AND company.track_record_at IS NULL AND company.track_record_version IS NULL)::bigint AS track_record_uncomputed,
               COUNT(*) FILTER (WHERE (company.track_record_json IS NULL) <> (company.track_record_at IS NULL) OR (company.track_record_json IS NULL) <> (company.track_record_version IS NULL))::bigint AS track_record_incoherent,
               MAX(company.updated_at) AS latest_observed_at
        FROM companies company LEFT JOIN companies target ON target.id = company.canonical_company_id
    """,
    "early_signals": """
        SELECT COUNT(*)::bigint AS total,
               COUNT(*) FILTER (WHERE NULLIF(BTRIM(external_id), '') IS NULL)::bigint AS missing_source_id,
               COUNT(*) FILTER (WHERE NULLIF(BTRIM(applicant), '') IS NULL)::bigint AS missing_applicant,
               COUNT(*) FILTER (WHERE NULLIF(BTRIM(address), '') IS NULL)::bigint AS missing_address,
               MAX(scraped_at) AS latest_observed_at
        FROM early_signal_events
    """,
    "news": """
        SELECT COUNT(*)::bigint AS total,
               COUNT(*) FILTER (WHERE NULLIF(BTRIM(title), '') IS NULL)::bigint AS missing_title,
               COUNT(*) FILTER (WHERE NULLIF(BTRIM(publisher), '') IS NULL)::bigint AS missing_publisher,
               COUNT(*) FILTER (WHERE NULLIF(BTRIM(date), '') IS NULL)::bigint AS missing_date,
               MAX(scraped_at) AS latest_observed_at
        FROM news
    """,
    "tender_matches": """
        SELECT COUNT(*)::bigint AS total,
               COUNT(*) FILTER (WHERE score < 0 OR score > 100)::bigint AS invalid_score,
               COUNT(*) FILTER (WHERE company_id IS NULL)::bigint AS missing_company_id,
               MAX(created_at) AS latest_observed_at
        FROM tender_matches
    """,
}

_DUPLICATE_SQL: dict[str, str] = {
    "permits": """
        SELECT COALESCE(NULLIF(BTRIM(source), ''), 'unknown') AS source,
               COALESCE(SUM(row_count - 1), 0)::bigint AS duplicate_source_key
        FROM (SELECT source, external_id, COUNT(*) AS row_count FROM permits
              WHERE NULLIF(BTRIM(external_id), '') IS NOT NULL
              GROUP BY source, external_id HAVING COUNT(*) > 1) duplicates
        GROUP BY 1 ORDER BY 1
    """,
    "tenders": """
        SELECT COALESCE(NULLIF(BTRIM(source), ''), 'unknown') AS source,
               COALESCE(SUM(row_count - 1), 0)::bigint AS duplicate_source_key
        FROM (SELECT source, tender_id, COUNT(*) AS row_count FROM tenders
              WHERE NULLIF(BTRIM(tender_id), '') IS NOT NULL
              GROUP BY source, tender_id HAVING COUNT(*) > 1) duplicates
        GROUP BY 1 ORDER BY 1
    """,
    "commercial_tenders": """
        SELECT COALESCE(NULLIF(BTRIM(source), ''), 'unknown') AS source,
               COALESCE(SUM(row_count - 1), 0)::bigint AS duplicate_source_key
        FROM (SELECT source, tender_id, COUNT(*) AS row_count FROM commercial_tenders
              WHERE NULLIF(BTRIM(tender_id), '') IS NOT NULL
              GROUP BY source, tender_id HAVING COUNT(*) > 1) duplicates
        GROUP BY 1 ORDER BY 1
    """,
    "contract_awards": """
        SELECT COALESCE(NULLIF(BTRIM(source), ''), 'unknown') AS source,
               COALESCE(SUM(row_count - 1), 0)::bigint AS duplicate_source_key
        FROM (SELECT source, external_id, COUNT(*) AS row_count FROM contract_awards
              WHERE NULLIF(BTRIM(external_id), '') IS NOT NULL
              GROUP BY source, external_id HAVING COUNT(*) > 1) duplicates
        GROUP BY 1 ORDER BY 1
    """,
}


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return value


def _row_dict(row: Any) -> dict[str, Any]:
    return {key: _json_value(value) for key, value in row._mapping.items()}


def _dataset_digest(value: Any) -> str:
    blob = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _finding(dataset: str, metric: str, value: int, severity: str) -> dict[str, Any]:
    if severity not in SEVERITIES:
        raise DataCoverageAuditError(f"invalid severity: {severity!r}")
    return {"dataset": dataset, "metric": metric, "value": value, "severity": severity}


def build_findings(datasets: dict[str, Any]) -> list[dict[str, Any]]:
    """Return deterministic findings from aggregate metrics only."""
    findings: list[dict[str, Any]] = []
    critical_metrics = {
        "dangling_company_fk",
        "dangling_canonical_fk",
        "track_record_incoherent",
    }
    high_metrics = {"duplicate_source_key", "alias_to_alias", "alias_missing_target"}
    for dataset, payload in sorted(datasets.items()):
        rows = payload if isinstance(payload, list) else [payload]
        for row in rows:
            total = int(row.get("total", 0))
            for metric, raw_value in sorted(row.items()):
                if metric in {"source", "total", "latest_observed_at"}:
                    continue
                if not isinstance(raw_value, int) or raw_value <= 0:
                    continue
                if metric in critical_metrics:
                    findings.append(_finding(dataset, metric, raw_value, "critical"))
                elif metric in high_metrics:
                    findings.append(_finding(dataset, metric, raw_value, "high"))
                elif total > 0 and metric.startswith("missing_") and raw_value == total:
                    findings.append(_finding(dataset, metric, raw_value, "critical"))
                elif metric.startswith("missing_") or metric in {
                    "unlinked",
                    "unscored",
                    "failed",
                }:
                    findings.append(_finding(dataset, metric, raw_value, "medium"))
    return sorted(
        findings,
        key=lambda x: (SEVERITIES.index(x["severity"]), x["dataset"], x["metric"]),
    )


def audit_data_coverage(session: Session, *, as_of: datetime) -> dict[str, Any]:
    """Execute bounded aggregate queries inside the caller-owned transaction."""
    if not isinstance(as_of, datetime) or as_of.tzinfo is None:
        raise DataCoverageAuditError("as_of must be a timezone-aware datetime")
    with session.no_autoflush:
        datasets: dict[str, Any] = {}
        for name, sql in _DATASET_SQL.items():
            datasets[name] = [_row_dict(row) for row in session.execute(text(sql))]
        for name, sql in _GLOBAL_SQL.items():
            row = session.execute(text(sql)).one()
            datasets[name] = _row_dict(row)
        duplicate_rows: list[dict[str, Any]] = []
        for dataset, sql in _DUPLICATE_SQL.items():
            for row in session.execute(text(sql)):
                item = _row_dict(row)
                item["dataset"] = dataset
                duplicate_rows.append(item)
        datasets["duplicate_source_keys"] = duplicate_rows
    findings = build_findings(datasets)
    digests = {name: _dataset_digest(value) for name, value in sorted(datasets.items())}
    return {
        "as_of": as_of.astimezone(timezone.utc).isoformat(),
        "datasets": datasets,
        "findings": findings,
        "finding_counts": {
            severity: sum(f["severity"] == severity for f in findings)
            for severity in SEVERITIES
        },
        "dataset_digests": digests,
    }
