"""Validated, read-only analytical query boundary.

This module deliberately does not accept SQL.  QuerySpec identifiers are mapped
through static allowlists and all values are bound parameters.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text

from db.connection import get_session

logger = logging.getLogger(__name__)

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
# Domains with a real province-level column. permits/tenders/early_signals
# have no province column at all -- a "BC"/"British Columbia" request
# against those domains must fail closed as an explicit capability gap
# (below), never silently drop the filter and return an unscoped total.
_PROVINCE_FIELDS = {
    "contract_awards": "winner_province",
    "companies": "primary_province",
}
_PROVINCE_VALUES = ("BC", "BRITISH COLUMBIA")
# V1 only ever means "BC" -- accept the two common spellings of that one
# province and normalize both to "BC". Any other province name (Ontario,
# Alberta, ...) is an explicit 422, never silently coerced into a BC scope.
_PROVINCE_ALIASES = {"BC": "BC", "BRITISH COLUMBIA": "BC"}
# A ranking row's evidence must point at real contributing rows, but an
# unbounded ARRAY_AGG can return thousands of ids for a popular company --
# cap what rides in the response payload; full drilldown stays available
# via operation=records.
_MAX_CONTRIBUTING_IDS = 100
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
    province: str | None = Field(
        default=None,
        max_length=40,
        description="Province-level scope (currently only 'BC' is meaningful).",
    )
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
        if self.province is not None:
            normalized = _PROVINCE_ALIASES.get(self.province.strip().upper())
            if normalized is None:
                # Any value other than BC/British Columbia is rejected
                # outright -- never silently swap a caller's real province
                # (e.g. "Ontario") for a BC scope they didn't ask for.
                raise ValueError(
                    f"unsupported province value '{self.province}' -- "
                    "only BC/British Columbia is supported"
                )
            if self.domain not in _PROVINCE_FIELDS:
                # Explicit capability gap -- never silently drop province
                # scope and answer with an unscoped total for a domain with
                # no real province column (permits, tenders, early_signals).
                raise ValueError(
                    f"province-wide geography is not supported for domain '{self.domain}'"
                )
            self.province = normalized
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


_COMPANY_ID_ALIAS = {"permits": "p", "contract_awards": "a"}


def _metric_sql(metric: str, table: str, spec: AnalyticsQuerySpec) -> str:
    if metric == "count":
        return "COUNT(*) AS count"
    if metric == "distinct_count":
        # Only permits/contract_awards carry a company_id column. The old
        # hardcoded "p.company_id"/else-"a.company_id" silently produced
        # invalid SQL (undefined alias) for companies/tenders/early_signals,
        # which the endpoint's generic except-block turned into an opaque
        # 503 with the real cause visible only in server logs.
        alias = _COMPANY_ID_ALIAS.get(spec.domain)
        if not alias:
            raise ValueError(f"{metric} is not available for {spec.domain}")
        return f"COUNT(DISTINCT {alias}.company_id) AS distinct_count"
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
    if spec.province:
        # Validated at the model level: province is only ever set for a
        # domain present in _PROVINCE_FIELDS.
        field = _PROVINCE_FIELDS[spec.domain]
        clauses.append(f"UPPER({alias}.{field}) = ANY(:province_values)")
        params["province_values"] = list(_PROVINCE_VALUES)
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


def _build_company_ranking_query(
    spec: AnalyticsQuerySpec,
    table: str,
    alias: str,
    dimensions: list[str],
    group_exprs: list[str],
    needs_canonical_join: bool,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Company-ranking aggregate (group_by company_id/canonical_company_id).

    Evidence is capped at the SQL level, not with ARRAY_AGG-everything-then-
    slice-in-Python: a per-row ROW_NUMBER() inside the CTE tags each
    contributing row, and the outer ARRAY_AGG(...) FILTER (WHERE rn <= cap)
    only ever materializes/transmits up to _MAX_CONTRIBUTING_IDS array
    elements, however many thousands of rows a popular company actually has.
    contributing_count is COUNT(*) over ALL matching rows (uncapped) so the
    disclosed total is always the true total, independent of the cap.
    """
    if spec.domain not in {"permits", "contract_awards"}:
        raise ValueError("company joins are not approved for this domain")
    join = f" JOIN companies c ON c.id = {alias}.company_id"
    if needs_canonical_join:
        join += " LEFT JOIN companies canonical ON canonical.id = c.canonical_company_id"

    group_names = [dim.partition(" AS ")[2] for dim in dimensions]

    raw_metric_cols: list[str] = []
    outer_metrics: list[str] = []
    for metric in spec.metrics:
        if metric == "count":
            outer_metrics.append("COUNT(*) AS count")
            continue
        if metric == "distinct_count":
            raise ValueError(
                "distinct_count is not supported combined with company ranking"
            )
        field = _VALUE_FIELDS.get(spec.domain)
        if not field:
            raise ValueError(f"{metric} is not available for {spec.domain}")
        # project_value is text in the permits table; only numeric values count.
        raw_expr = field
        if spec.domain == "permits":
            raw_expr = (
                "CASE WHEN project_value ~ '^[^0-9]*[0-9]+([.,][0-9]+)?[^0-9]*$' "
                "THEN NULLIF(regexp_replace(project_value, '[^0-9.]', '', 'g'), '')::numeric END"
            )
        col_name = f"raw_{metric}"
        raw_metric_cols.append(f"{raw_expr} AS {col_name}")
        outer_metrics.append(f"{metric.upper()}({col_name}) AS {metric}")

    clauses, params = _where_clauses(spec, alias)
    cte_select = (
        dimensions
        + [f"{alias}.id AS contributing_id"]
        + raw_metric_cols
        + [
            "ROW_NUMBER() OVER (PARTITION BY "
            + ", ".join(group_exprs)
            + f" ORDER BY {alias}.id) AS rn"
        ]
    )
    cte_sql = f"SELECT {', '.join(cte_select)} FROM {table} {alias}{join}"
    if clauses:
        cte_sql += " WHERE " + " AND ".join(clauses)

    outer_select = (
        group_names
        + outer_metrics
        + [
            "COUNT(*) AS contributing_count",
            f"ARRAY_AGG(contributing_id ORDER BY contributing_id) "
            f"FILTER (WHERE rn <= {_MAX_CONTRIBUTING_IDS}) AS contributing_ids",
            f"COUNT(*) > {_MAX_CONTRIBUTING_IDS} AS evidence_truncated",
        ]
    )
    sql = (
        f"WITH ranked AS ({cte_sql}) "
        f"SELECT {', '.join(outer_select)} FROM ranked "
        f"GROUP BY {', '.join(group_names)}"
    )
    if spec.order_by:
        direction = "DESC" if spec.order_by in spec.metrics else "ASC"
        sql += f" ORDER BY {spec.order_by} {direction}"
    sql += " LIMIT :result_limit"
    params["result_limit"] = spec.limit
    provenance = {
        "operation": "aggregate",
        "source_tables": [table, "companies"],
        "event_time_field": spec.date_field,
        "filters": spec.filters,
        "geography": spec.geography,
        "province": spec.province,
        "lookback_days": spec.lookback_days,
        "read_only": True,
    }
    return sql, params, provenance


def _build_aggregate_query(
    spec: AnalyticsQuerySpec,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    table = _TABLES[spec.domain]
    alias = _alias_for(spec.domain)
    # SELECT needs the aliased "expr AS name" form; GROUP BY must repeat the
    # bare expression -- "GROUP BY expr AS name" is a Postgres syntax error.
    # Reusing `dimensions` (with the AS-suffix) for GROUP BY was the actual
    # 503 cause: any group_by containing company_id/canonical_company_id
    # (ranking-by-company queries) always failed with a real SQL syntax
    # error, caught only by the endpoint's generic except-block.
    dimensions: list[str] = []
    group_exprs: list[str] = []
    ranks_by_company = False
    needs_canonical_join = False
    for group in spec.group_by:
        if group == "canonical_company_id":
            # Ranking must aggregate by the CANONICAL company, not the raw
            # row: two alias rows of the same real company (e.g. a DBA
            # standalone row and its canonical parent) must collapse into
            # one ranking position, not occupy two. canonical_company_name
            # is resolved alongside it -- the caller should never have to
            # make a second lookup just to know whose count this is.
            id_expr = "COALESCE(c.canonical_company_id, c.id)"
            # NULLIF(..., '') before each display_name: an empty-string
            # display_name (vs. NULL) must not win over a real `name` value
            # -- COALESCE alone treats '' as non-null and would return an
            # empty canonical_company_name even though name is populated.
            name_expr = (
                "COALESCE(NULLIF(canonical.display_name, ''), canonical.name, "
                "NULLIF(c.display_name, ''), c.name)"
            )
            dimensions.append(f"{id_expr} AS canonical_company_id")
            dimensions.append(f"{name_expr} AS canonical_company_name")
            group_exprs.append(id_expr)
            group_exprs.append(name_expr)
            ranks_by_company = True
            needs_canonical_join = True
        elif group == "company_id":
            expr = f"{alias}.company_id"
            dimensions.append(f"{expr} AS company_id")
            group_exprs.append(expr)
            ranks_by_company = True
        else:
            dimensions.append(group)
            group_exprs.append(group)

    if ranks_by_company:
        return _build_company_ranking_query(
            spec, table, alias, dimensions, group_exprs, needs_canonical_join
        )

    metrics = [_metric_sql(metric, table, spec) for metric in spec.metrics]
    clauses, params = _where_clauses(spec, alias)
    sql = f"SELECT {', '.join(dimensions + metrics)} FROM {table} {alias}"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    if group_exprs:
        sql += " GROUP BY " + ", ".join(group_exprs)
    if spec.order_by:
        direction = "DESC" if spec.order_by in spec.metrics else "ASC"
        sql += f" ORDER BY {spec.order_by} {direction}"
    sql += " LIMIT :result_limit"
    params["result_limit"] = spec.limit
    provenance = {
        "operation": "aggregate",
        "source_tables": [table],
        "event_time_field": spec.date_field,
        "filters": spec.filters,
        "geography": spec.geography,
        "province": spec.province,
        "lookback_days": spec.lookback_days,
        "read_only": True,
    }
    return sql, params, provenance


def _unresolved_count_query(
    spec: AnalyticsQuerySpec, alias: str
) -> tuple[str, dict[str, Any]] | None:
    """Ranking by company_id/canonical_company_id joins through an
    INNER JOIN on companies -- rows with no company attribution at all
    (company_id IS NULL) are silently excluded from every ranking position.
    Count them separately, under the exact same filters, so the response
    can disclose "N permits/awards had no company attribution" instead of
    quietly under-reporting."""
    if not ({"company_id", "canonical_company_id"} & set(spec.group_by)):
        return None
    table = _TABLES[spec.domain]
    clauses, params = _where_clauses(spec, alias)
    clauses.append(f"{alias}.company_id IS NULL")
    sql = f"SELECT COUNT(*) AS unresolved_count FROM {table} {alias} WHERE " + (
        " AND ".join(clauses)
    )
    return sql, params


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
        "province": spec.province,
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

        unresolved_count: int | None = None
        if spec.operation == "aggregate":
            unresolved = _unresolved_count_query(spec, _alias_for(spec.domain))
            if unresolved is not None:
                u_sql, u_params = unresolved
                unresolved_count = connection.execute(
                    text(u_sql), u_params
                ).scalar_one()
                provenance["unresolved_count"] = unresolved_count

        is_records = spec.operation == "records"
        source_table = provenance["source_tables"][0]
        evidence_refs: list[str] = []
        for row in rows:
            contributing = row.get("contributing_ids")
            if contributing is not None:
                # Real contributing row ids, not a repeat of the aggregate --
                # this is the evidence behind a canonical-company ranking row.
                # contributing_count/contributing_ids/evidence_truncated are
                # already computed and capped at the SQL level (ROW_NUMBER()
                # + ARRAY_AGG(...) FILTER) -- no unbounded ARRAY_AGG is ever
                # built and sliced here. Full drilldown remains available
                # via operation=records.
                evidence_refs.extend(
                    f"{source_table}:{cid}" for cid in contributing if cid is not None
                )
            else:
                evidence_refs.append(f"{source_table}:{row.get('id', 'aggregate')}")

        data_gaps = [] if rows else ["no matching records"]
        if unresolved_count:
            data_gaps = [
                *data_gaps,
                f"{unresolved_count} row(s) have no company attribution and "
                "are excluded from this ranking",
            ]

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
            "evidence_refs": evidence_refs,
            "provenance": provenance,
            "confidence": "medium" if rows else "low",
            "data_gaps": data_gaps,
        }
    except Exception:
        session.rollback()
        # Full cause goes to server logs only -- the API response never
        # leaks DB/internal details (query text, table names, driver
        # errors) to the caller.
        logger.exception(
            "analytics query failed domain=%s operation=%s",
            spec.domain,
            spec.operation,
        )
        raise HTTPException(
            status_code=503, detail="analytics query unavailable"
        ) from None
    finally:
        session.close()
