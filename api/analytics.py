"""Validated, read-only analytical query boundary.

This module deliberately does not accept SQL.  QuerySpec identifiers are mapped
through static allowlists and all values are bound parameters.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text

from db.connection import get_session

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

_DOMAINS = {"permits", "contract_awards", "companies", "tenders", "early_signals"}
_METRICS = {"count", "sum", "avg", "min", "max", "distinct_count"}
_GROUPS = {
    "permits": {"city", "permit_type", "company_id", "canonical_company_id"},
    "contract_awards": {
        "winner_city",
        "delivery_region",
        "company_id",
        "canonical_company_id",
        "procurement_category",
    },
    "companies": {"primary_city", "primary_province", "primary_trade", "company_type"},
    "tenders": {"location", "source", "category"},
    "early_signals": {"municipality", "region", "signal_type"},
}
_DATE_FIELDS = {
    "permits": {"issue_date", "application_date"},
    "contract_awards": {"award_date"},
    "companies": set(),
    "tenders": {"posted_date", "closing_date"},
    "early_signals": {"transaction_date"},
}
_VALUE_FIELDS = {
    "permits": "project_value",
    "contract_awards": "award_value",
}
_TABLES = {
    "permits": "permits",
    "contract_awards": "contract_awards",
    "companies": "companies",
    "tenders": "tenders",
    "early_signals": "early_signal_events",
}
# Fields a "records" (drilldown) query may return. Deliberately narrower than
# every column on the table -- no raw-SQL, no arbitrary joins, no unvetted
# columns. "id" is always added by build_query even if omitted here, so every
# record row can carry a stable evidence_ref.
_RECORD_FIELDS = {
    "permits": {
        "id",
        "address",
        "permit_type",
        "project_value",
        "applicant",
        "contractor",
        "issue_date",
        "application_date",
        "city",
        "company_id",
        "source",
    },
    "contract_awards": {
        "id",
        "title",
        "winner_company",
        "winner_city",
        "delivery_region",
        "award_value",
        "award_date",
        "procurement_category",
        "company_id",
        "url",
    },
    "companies": {
        "id",
        "name",
        "primary_city",
        "primary_province",
        "primary_trade",
        "company_type",
    },
    "tenders": {
        "id",
        "title",
        "organization",
        "category",
        "posted_date",
        "closing_date",
        "estimated_value",
        "location",
        "source",
        "url",
    },
    "early_signals": {
        "id",
        "signal_type",
        "municipality",
        "region",
        "transaction_date",
        "address",
        "applicant",
        "project_value",
        "url_link",
    },
}
_IDENTIFIER = re.compile(r"^[a-z_]+$")
_OPERATIONS = {"aggregate", "records"}


class AnalyticsQuerySpec(BaseModel):
    domain: str
    operation: str = "aggregate"
    metrics: list[str] = Field(default_factory=list, max_length=4)
    fields: list[str] = Field(default_factory=list, max_length=10)
    filters: dict[str, str | int | float | bool] = Field(default_factory=dict)
    geography: str | None = Field(default=None, max_length=120)
    date_field: str | None = None
    lookback_days: int | None = Field(default=None, ge=1, le=3650)
    group_by: list[str] = Field(default_factory=list, max_length=3)
    order_by: str | None = None
    limit: int = Field(default=100, ge=1, le=500)

    @model_validator(mode="after")
    def validate_allowlist(self) -> "AnalyticsQuerySpec":
        if self.domain not in _DOMAINS:
            raise ValueError("unsupported analytics domain")
        if self.operation not in _OPERATIONS:
            raise ValueError("unsupported analytics operation")
        if any(group not in _GROUPS[self.domain] for group in self.group_by):
            raise ValueError("unsupported group_by dimension")
        if (
            self.date_field is not None
            and self.date_field not in _DATE_FIELDS[self.domain]
        ):
            raise ValueError("invalid event-time field for domain")
        if self.lookback_days is not None and self.date_field is None:
            raise ValueError("date_field is required with lookback_days")
        allowed_filters = set(_GROUPS[self.domain])
        if self.domain != "companies":
            allowed_filters.add("source")
        if self.domain in {"permits", "contract_awards"}:
            allowed_filters |= {"company_id"}
        if any(
            key not in allowed_filters or not _IDENTIFIER.fullmatch(key)
            for key in self.filters
        ):
            raise ValueError("unsupported filter")
        if self.operation == "aggregate":
            if not self.metrics:
                raise ValueError("aggregate operation requires at least one metric")
            if any(metric not in _METRICS for metric in self.metrics):
                raise ValueError("unsupported analytics metric")
            if self.fields:
                raise ValueError("fields is only valid with operation=records")
            if self.order_by and self.order_by not in set(self.group_by) | set(
                self.metrics
            ):
                raise ValueError(
                    "order_by must reference a selected metric or dimension"
                )
        else:  # records
            if self.metrics:
                raise ValueError("metrics is only valid with operation=aggregate")
            if self.group_by:
                raise ValueError("group_by is only valid with operation=aggregate")
            record_fields = _RECORD_FIELDS[self.domain]
            if any(f not in record_fields for f in self.fields):
                raise ValueError("unsupported record field for domain")
            if self.order_by and self.order_by not in record_fields:
                raise ValueError("order_by must reference an allowlisted record field")
        return self


def _event_date_sql(field: str) -> str:
    # Date columns are legacy VARCHAR values.  Guard malformed values before
    # casting so bad source rows become an explicit data gap, not a query error.
    return (
        f"CASE WHEN {field} ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$' "
        f"THEN CAST({field} AS date) END"
    )


def _metric_sql(metric: str, table: str, spec: AnalyticsQuerySpec) -> str:
    if metric == "count":
        return "COUNT(*) AS count"
    if metric == "distinct_count":
        column = "p.company_id" if spec.domain == "permits" else "a.company_id"
        return f"COUNT(DISTINCT {column}) AS distinct_count"
    field = _VALUE_FIELDS.get(spec.domain)
    if not field:
        raise ValueError(f"{metric} is not available for {spec.domain}")
    # project_value is text in the permits table; only numeric values count.
    value_expr = field
    if spec.domain == "permits":
        value_expr = (
            "CASE WHEN project_value ~ '^[^0-9]*[0-9]+([.,][0-9]+)?[^0-9]*$' "
            "THEN NULLIF(regexp_replace(project_value, '[^0-9.]', '', 'g'), '')::numeric END"
        )
    return f"{metric.upper()}({value_expr}) AS {metric}"


def _alias_for(domain: str) -> str:
    return "p" if domain == "permits" else "a" if domain == "contract_awards" else "t"


def _where_clauses(
    spec: AnalyticsQuerySpec, alias: str
) -> tuple[list[str], dict[str, Any]]:
    """Geography/filter/event-time predicates shared by both aggregate and
    records queries -- identical safety contract for either operation."""
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if spec.geography:
        if spec.domain == "permits":
            clauses.append("p.city = :geography")
        elif spec.domain == "contract_awards":
            clauses.append(
                "(a.winner_city = :geography OR a.delivery_region = :geography)"
            )
        elif spec.domain == "early_signals":
            clauses.append("(t.municipality = :geography OR t.region = :geography)")
        elif spec.domain == "companies":
            clauses.append(
                "(t.primary_city = :geography OR t.primary_province = :geography)"
            )
        else:
            clauses.append("t.location ILIKE :geography_pattern")
            params["geography_pattern"] = f"%{spec.geography.strip()}%"
        params["geography"] = spec.geography.strip()
    for key, value in spec.filters.items():
        # Filter identifiers have already been validated; values remain bound.
        if spec.domain == "contract_awards" and key == "city":
            clauses.append(
                "(a.winner_city = :filter_city OR a.delivery_region = :filter_city)"
            )
        else:
            clauses.append(f"{alias}.{key} = :filter_{key}")
        params[f"filter_{key}"] = value
    if spec.date_field and spec.lookback_days:
        expr = _event_date_sql(f"{alias}.{spec.date_field}")
        clauses.append(f"{expr} >= :start_date AND {expr} < :end_date")
        end = date.today()
        params["start_date"] = end - timedelta(days=spec.lookback_days)
        params["end_date"] = end + timedelta(days=1)
    return clauses, params


def _build_aggregate_query(
    spec: AnalyticsQuerySpec,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    table = _TABLES[spec.domain]
    alias = _alias_for(spec.domain)
    dimensions: list[str] = []
    for group in spec.group_by:
        if group == "canonical_company_id":
            dimensions.append(
                "COALESCE(c.canonical_company_id, c.id) AS canonical_company_id"
            )
        elif group == "company_id":
            dimensions.append(f"{alias}.company_id AS company_id")
        else:
            dimensions.append(group)
    metrics = [_metric_sql(metric, table, spec) for metric in spec.metrics]
    join = ""
    if "company_id" in spec.group_by or "canonical_company_id" in spec.group_by:
        if spec.domain not in {"permits", "contract_awards"}:
            raise ValueError("company joins are not approved for this domain")
        join = f" JOIN companies c ON c.id = {alias}.company_id"
    clauses, params = _where_clauses(spec, alias)
    sql = f"SELECT {', '.join(dimensions + metrics)} FROM {table} {alias}{join}"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    if dimensions:
        sql += " GROUP BY " + ", ".join(dimensions)
    if spec.order_by:
        direction = "DESC" if spec.order_by in spec.metrics else "ASC"
        sql += f" ORDER BY {spec.order_by} {direction}"
    sql += " LIMIT :result_limit"
    params["result_limit"] = spec.limit
    provenance = {
        "operation": "aggregate",
        "source_tables": [table] + (["companies"] if join else []),
        "event_time_field": spec.date_field,
        "filters": spec.filters,
        "geography": spec.geography,
        "lookback_days": spec.lookback_days,
        "read_only": True,
    }
    return sql, params, provenance


def _build_records_query(
    spec: AnalyticsQuerySpec,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Underlying-record drilldown: allowlisted flat fields on a single table,
    no joins, no group_by, no arbitrary expressions. Always orders newest/
    largest-id first -- this is the "show me the N most recent ones" mode."""
    table = _TABLES[spec.domain]
    alias = _alias_for(spec.domain)
    fields = spec.fields or sorted(_RECORD_FIELDS[spec.domain])
    if "id" not in fields:
        fields = ["id", *fields]
    columns = ", ".join(f"{alias}.{field} AS {field}" for field in fields)
    clauses, params = _where_clauses(spec, alias)
    sql = f"SELECT {columns} FROM {table} {alias}"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    order_field = spec.order_by or spec.date_field or "id"
    sql += f" ORDER BY {alias}.{order_field} DESC"
    sql += " LIMIT :result_limit"
    params["result_limit"] = spec.limit
    provenance = {
        "operation": "records",
        "source_tables": [table],
        "event_time_field": spec.date_field,
        "filters": spec.filters,
        "geography": spec.geography,
        "lookback_days": spec.lookback_days,
        "fields": fields,
        "read_only": True,
    }
    return sql, params, provenance


def build_query(spec: AnalyticsQuerySpec) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Build SQL and provenance from a validated spec; never accepts SQL input."""
    if spec.operation == "records":
        return _build_records_query(spec)
    return _build_aggregate_query(spec)


@router.post("/query")
def analytics_query(spec: AnalyticsQuerySpec) -> dict[str, Any]:
    try:
        sql, params, provenance = build_query(spec)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session = get_session()
    try:
        connection = session.connection()
        connection.execute(text("SET TRANSACTION READ ONLY"))
        connection.execute(text("SET LOCAL statement_timeout = '5s'"))
        rows = [
            dict(row) for row in connection.execute(text(sql), params).mappings().all()
        ]
        is_records = spec.operation == "records"
        return {
            "rows": rows,
            "aggregates": (
                {}
                if is_records
                else {
                    key: value
                    for key, value in (rows[0].items() if len(rows) == 1 else [])
                }
            ),
            "evidence_refs": [
                f"{provenance['source_tables'][0]}:{row.get('id', 'aggregate')}"
                for row in rows
            ],
            "provenance": provenance,
            "confidence": "medium" if rows else "low",
            "data_gaps": ([] if rows else ["no matching records"]),
        }
    except Exception as exc:
        session.rollback()
        raise HTTPException(
            status_code=503, detail="analytics query unavailable"
        ) from exc
    finally:
        session.close()
