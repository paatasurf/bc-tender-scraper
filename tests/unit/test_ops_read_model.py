"""Unit tests for pipeline/ops_read_model.py -- the Mission Control M1
read-only ops API's data-shaping logic.

Pure-logic tests (normalization, JSON parsing, error classification,
freshness thresholds) need no database. DB-touching functions (coordinator
lookup, source freshness) are tested two ways: a fast MagicMock-session
path for the "missing telemetry" / "missing schema" / "expired lease"
degrade-gracefully behavior, and a real local-Postgres-gated path (skipped
if unavailable, same convention as tests/unit/test_pipeline_coordinator_db.py)
for genuine end-to-end correctness.
"""

from __future__ import annotations

import inspect
import json
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, text

from db.pipeline_coordinator_ddl import pipeline_coordinator_migration_statements
from pipeline import ops_read_model as rm
from tests.db_test_safety import require_local_test_database

# ---------------------------------------------------------------------
# Pure logic: counts_json
# ---------------------------------------------------------------------


def test_parse_counts_json_missing_returns_empty_dict():
    assert rm.parse_counts_json(None) == {}
    assert rm.parse_counts_json("") == {}


def test_parse_counts_json_malformed_returns_empty_dict():
    assert rm.parse_counts_json("{not valid json") == {}
    assert rm.parse_counts_json("not json at all") == {}


def test_parse_counts_json_non_object_json_returns_empty_dict():
    # Valid JSON, but not a dict -- must not be returned as-is.
    assert rm.parse_counts_json("[1, 2, 3]") == {}
    assert rm.parse_counts_json('"just a string"') == {}
    assert rm.parse_counts_json("42") == {}


def test_parse_counts_json_valid_object_passes_through():
    assert rm.parse_counts_json('{"found": 5, "new": 2}') == {"found": 5, "new": 2}


# ---------------------------------------------------------------------
# P0 -- error classification never leaks raw text (security)
# ---------------------------------------------------------------------

_VALID_ERROR_SUMMARIES = frozenset(
    {"timeout", "http_4xx", "http_5xx", "database", "validation", "unknown"}
)


def test_classify_run_error_none_or_empty_is_not_present():
    assert rm.classify_run_error(None) == (False, None)
    assert rm.classify_run_error("") == (False, None)


def test_classify_run_error_returns_only_fixed_labels():
    for raw in (
        "connection timed out after 30s",
        "HTTP 503 Service Unavailable",
        "HTTP 404 Not Found",
        "psycopg2.OperationalError: could not connect to server",
        "ValueError: invalid literal for int()",
        "completely unrecognized failure mode",
    ):
        present, summary = rm.classify_run_error(raw)
        assert present is True
        assert summary in _VALID_ERROR_SUMMARIES


@pytest.mark.parametrize(
    "raw_secret",
    [
        "postgresql://user:password@host/db",
        "Authorization: Bearer secret-value",
        "api_key=secret-value",
        "Failed to connect: postgresql://scraper_user:hunter2@10.0.0.5:5432/production",
        "External call failed -- Authorization: Bearer sk_live_abcdef123456",
    ],
)
def test_classify_run_error_never_leaks_secret_fragments(raw_secret):
    present, summary = rm.classify_run_error(raw_secret)
    assert present is True
    # The ENTIRE return value set must never contain any substring of the
    # original secret-bearing text -- only one of the fixed labels.
    assert summary in _VALID_ERROR_SUMMARIES
    for leaked_marker in (
        "password",
        "hunter2",
        "secret-value",
        "sk_live",
        "Bearer",
        "user:",
    ):
        assert leaked_marker not in summary


def test_build_run_payload_never_includes_raw_error_field():
    record = SimpleNamespace(
        id=1,
        run_id="r1",
        step="scrape-federal",
        status="failed",
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        finished_at=datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
        error="postgresql://user:hunter2@host/db failed; Authorization: Bearer secret-value",
        counts_json="{}",
    )
    payload = rm.build_run_payload(record, coordinator_active_run_ids=frozenset())

    assert "error" not in payload
    assert payload["error_present"] is True
    assert payload["error_summary"] in _VALID_ERROR_SUMMARIES

    serialized = json.dumps(payload)
    for leaked_marker in (
        "hunter2",
        "secret-value",
        "user:hunter2",
        "Bearer secret-value",
    ):
        assert (
            leaked_marker not in serialized
        ), f"leaked {leaked_marker!r} into API payload"


def test_build_run_payload_no_error_is_error_present_false():
    record = SimpleNamespace(
        id=1,
        run_id="r1",
        step="scrape-federal",
        status="success",
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        finished_at=datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
        error=None,
        counts_json="{}",
    )
    payload = rm.build_run_payload(record, coordinator_active_run_ids=frozenset())
    assert payload["error_present"] is False
    assert payload["error_summary"] is None


# ---------------------------------------------------------------------
# Pure logic: normalize_pipeline_run_status
# ---------------------------------------------------------------------


def test_normalize_terminal_statuses_pass_through():
    for status in ("success", "failed", "skipped"):
        assert (
            rm.normalize_pipeline_run_status(
                status=status,
                finished_at=datetime.now(timezone.utc),
                run_id="r1",
                coordinator_active_run_ids=frozenset(),
            )
            == status
        )


def test_normalize_running_without_finished_at_and_no_coordinator_is_stale_candidate():
    result = rm.normalize_pipeline_run_status(
        status="running",
        finished_at=None,
        run_id="r1",
        coordinator_active_run_ids=frozenset(),
    )
    assert result == "stale_candidate"


def test_normalize_running_with_finished_at_but_no_coordinator_is_unknown():
    result = rm.normalize_pipeline_run_status(
        status="running",
        finished_at=datetime.now(timezone.utc),
        run_id="r1",
        coordinator_active_run_ids=frozenset(),
    )
    assert result == "unknown"


def test_normalize_unrecognized_status_is_unknown():
    result = rm.normalize_pipeline_run_status(
        status="totally-made-up-status",
        finished_at=None,
        run_id="r1",
        coordinator_active_run_ids=frozenset(),
    )
    assert result == "unknown"


def test_coordinator_active_lease_takes_priority_over_stale_candidate():
    """The core rule from task item 5: a running row with no finished_at
    would normally be stale_candidate, but a matching coordinator-backed
    active lease must take priority and produce "active" instead."""
    result_without_lease = rm.normalize_pipeline_run_status(
        status="running",
        finished_at=None,
        run_id="r1",
        coordinator_active_run_ids=frozenset(),
    )
    result_with_lease = rm.normalize_pipeline_run_status(
        status="running",
        finished_at=None,
        run_id="r1",
        coordinator_active_run_ids=frozenset({"r1"}),
    )
    assert result_without_lease == "stale_candidate"
    assert result_with_lease == "active"


def test_coordinator_lease_only_applies_to_matching_run_id():
    # A lease for a DIFFERENT run_id must not leak "active" onto this one.
    result = rm.normalize_pipeline_run_status(
        status="running",
        finished_at=None,
        run_id="r1",
        coordinator_active_run_ids=frozenset({"some-other-run"}),
    )
    assert result == "stale_candidate"


# ---------------------------------------------------------------------
# Pure logic: build_run_payload shape
# ---------------------------------------------------------------------


def _fake_record(**overrides):
    base = dict(
        id=1,
        run_id="r1",
        step="scrape-federal",
        status="success",
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        finished_at=datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
        error=None,
        counts_json='{"found": 3}',
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_build_run_payload_shapes_fields():
    record = _fake_record()
    payload = rm.build_run_payload(record, coordinator_active_run_ids=frozenset())
    assert payload["run_id"] == "r1"
    assert payload["job_type"] == "scrape-federal"  # step -> job_type mapping
    assert payload["status"] == "success"
    assert payload["normalized_status"] == "success"
    assert payload["counts"] == {"found": 3}
    assert payload["error_present"] is False
    assert payload["error_summary"] is None
    assert payload["started_at"] == "2026-01-01T00:00:00+00:00"


def test_build_run_payload_stale_candidate_end_to_end():
    record = _fake_record(status="running", finished_at=None, error=None)
    payload = rm.build_run_payload(record, coordinator_active_run_ids=frozenset())
    assert payload["status"] == "running"  # raw value unchanged
    assert payload["normalized_status"] == "stale_candidate"


# ---------------------------------------------------------------------
# Pure logic: freshness status thresholds
# ---------------------------------------------------------------------


def test_freshness_status_thresholds():
    assert rm._freshness_status(None) == "unknown"
    assert rm._freshness_status(0.0) == "healthy"
    assert rm._freshness_status(rm.FRESHNESS_HEALTHY_HOURS) == "healthy"
    assert rm._freshness_status(rm.FRESHNESS_HEALTHY_HOURS + 0.01) == "degraded"
    assert rm._freshness_status(rm.FRESHNESS_DEGRADED_HOURS) == "degraded"
    assert rm._freshness_status(rm.FRESHNESS_DEGRADED_HOURS + 0.01) == "stale"


# ---------------------------------------------------------------------
# P2 -- MERX Open is its own freshness source, separate from Federal
# ---------------------------------------------------------------------


def test_merx_open_is_a_separate_freshness_source_from_federal():
    names = [s.name for s in rm.FRESHNESS_SOURCES]
    assert "Federal" in names
    assert "MERX Open" in names
    assert "MERX Architecture" in names

    federal = next(s for s in rm.FRESHNESS_SOURCES if s.name == "Federal")
    merx_open = next(s for s in rm.FRESHNESS_SOURCES if s.name == "MERX Open")
    assert federal.filter_value == "buyandsell.gc.ca"
    assert merx_open.filter_value == "merx.com"
    assert federal.model is merx_open.model  # same table, different source filter


# ---------------------------------------------------------------------
# DB access, mocked session: missing telemetry / missing schema
# ---------------------------------------------------------------------


def test_compute_source_freshness_no_rows_returns_unknown_with_reason():
    session = MagicMock()
    session.execute.return_value.scalar_one_or_none.return_value = None
    source = rm.FRESHNESS_SOURCES[0]

    result = rm.compute_source_freshness(session, source)

    assert result["status"] == "unknown"
    assert result["latest_record_at"] is None
    assert result["freshness_hours"] is None
    assert result["reason"] == "telemetry_not_available"
    assert "source_of_truth" in result
    assert "last_success_at" not in result  # renamed field must not linger


def test_compute_source_freshness_query_failure_degrades_to_unknown():
    session = MagicMock()
    session.execute.side_effect = RuntimeError("db exploded")
    source = rm.FRESHNESS_SOURCES[0]

    result = rm.compute_source_freshness(session, source)  # must not raise

    assert result["status"] == "unknown"
    assert result["reason"] == "telemetry_not_available"


def test_coordinator_schema_available_false_on_query_failure():
    session = MagicMock()
    session.execute.side_effect = RuntimeError("relation does not exist")
    assert rm.coordinator_schema_available(session) is False


def test_get_coordinator_active_run_ids_empty_when_schema_unavailable():
    session = MagicMock()
    session.execute.return_value.scalar_one.return_value = False
    result = rm.get_coordinator_active_run_ids(session)
    assert result == frozenset()


def test_get_coordinator_summary_degrades_when_schema_unavailable():
    session = MagicMock()
    session.execute.return_value.scalar_one.return_value = False
    summary = rm.get_coordinator_summary(session, backend="postgres")
    assert summary == {
        "backend": "postgres",
        "schema_available": False,
        "active_run": None,
        "expired_lease_run": None,
    }


# ---------------------------------------------------------------------
# P1 -- expired lease must never be reported as active_run
# ---------------------------------------------------------------------


def test_expired_lease_active_row_is_not_reported_as_active_run():
    """status='active' in the DB, but the lease is in the past: this must
    show up as expired_lease_run, and active_run must be None. This is
    the exact "hung process looks alive" failure Mission Control must not
    repeat."""
    session = MagicMock()
    schema_check_result = MagicMock()
    schema_check_result.scalar_one.return_value = True

    row_mapping = MagicMock()
    row_mapping.mappings.return_value.first.return_value = {
        "run_id": "hung-run-id",
        "phase": "import",
        "lease_expires_at": datetime.now(timezone.utc) - timedelta(hours=1),
        "created_at": datetime.now(timezone.utc) - timedelta(hours=5),
        "success": None,
        "stale_reclaimed": False,
        "error": "",
    }

    session.execute.side_effect = [schema_check_result, row_mapping]

    summary = rm.get_coordinator_summary(session, backend="postgres")

    assert summary["active_run"] is None
    assert summary["expired_lease_run"] is not None
    assert summary["expired_lease_run"]["run_id"] == "hung-run-id"
    assert summary["expired_lease_run"]["lease_valid"] is False


def test_valid_lease_active_row_is_reported_as_active_run_not_expired():
    session = MagicMock()
    schema_check_result = MagicMock()
    schema_check_result.scalar_one.return_value = True

    row_mapping = MagicMock()
    row_mapping.mappings.return_value.first.return_value = {
        "run_id": "live-run-id",
        "phase": "import",
        "lease_expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        "created_at": datetime.now(timezone.utc) - timedelta(minutes=5),
        "success": None,
        "stale_reclaimed": False,
        "error": "",
    }

    session.execute.side_effect = [schema_check_result, row_mapping]

    summary = rm.get_coordinator_summary(session, backend="postgres")

    assert summary["active_run"] is not None
    assert summary["active_run"]["run_id"] == "live-run-id"
    assert summary["active_run"]["lease_valid"] is True
    assert summary["expired_lease_run"] is None
    # (M2B) new fields present with honest values, no crash on lookup.
    assert summary["active_run"]["success"] is None
    assert summary["active_run"]["stale_reclaimed"] is False
    assert summary["active_run"]["error_present"] is False
    assert summary["active_run"]["error_summary"] is None


# ---------------------------------------------------------------------
# M2B -- coordinator error/success never leaks raw text
# ---------------------------------------------------------------------


def test_lease_row_payload_never_leaks_raw_coordinator_error():
    session = MagicMock()
    schema_check_result = MagicMock()
    schema_check_result.scalar_one.return_value = True

    row_mapping = MagicMock()
    row_mapping.mappings.return_value.first.return_value = {
        "run_id": "r1",
        "phase": "import",
        "lease_expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        "created_at": datetime.now(timezone.utc),
        "success": False,
        "stale_reclaimed": False,
        "error": "postgresql://user:hunter2@host/db failed; Authorization: Bearer secret-value",
    }
    session.execute.side_effect = [schema_check_result, row_mapping]

    summary = rm.get_coordinator_summary(session, backend="postgres")
    active_run = summary["active_run"]

    assert "error" not in active_run
    assert active_run["error_present"] is True
    assert active_run["error_summary"] in _VALID_ERROR_SUMMARIES
    assert active_run["success"] is False

    serialized = json.dumps(summary)
    for leaked_marker in (
        "hunter2",
        "secret-value",
        "user:hunter2",
        "Bearer secret-value",
    ):
        assert (
            leaked_marker not in serialized
        ), f"leaked {leaked_marker!r} into API payload"


def test_lease_row_payload_no_error_is_error_present_false():
    session = MagicMock()
    schema_check_result = MagicMock()
    schema_check_result.scalar_one.return_value = True

    row_mapping = MagicMock()
    row_mapping.mappings.return_value.first.return_value = {
        "run_id": "r1",
        "phase": "import",
        "lease_expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        "created_at": datetime.now(timezone.utc),
        "success": True,
        "stale_reclaimed": False,
        "error": "",
    }
    session.execute.side_effect = [schema_check_result, row_mapping]

    summary = rm.get_coordinator_summary(session, backend="postgres")
    active_run = summary["active_run"]

    assert active_run["error_present"] is False
    assert active_run["error_summary"] is None
    assert active_run["success"] is True


# ---------------------------------------------------------------------
# M2B -- container_lock: independent signal, never blended with
# coordinator active/expired semantics
# ---------------------------------------------------------------------


def test_container_lock_status_active_when_read_succeeds_and_held(monkeypatch):
    monkeypatch.setattr(rm, "pipeline_status", lambda: {"running": True, "pid": 4242})
    result = rm.get_container_lock_status()
    assert result == {
        "status": "active",
        "running": True,
        "scope": "current_container",
        "pid": 4242,
    }


def test_container_lock_status_idle_when_read_succeeds_and_not_held(monkeypatch):
    monkeypatch.setattr(rm, "pipeline_status", lambda: {"running": False, "pid": 0})
    result = rm.get_container_lock_status()
    assert result == {
        "status": "idle",
        "running": False,
        "scope": "current_container",
        "pid": None,
    }


def test_container_lock_status_unknown_on_read_failure_never_leaks_exception_text(
    monkeypatch,
):
    def _boom():
        raise RuntimeError("filesystem unavailable: /some/sensitive/path denied")

    monkeypatch.setattr(rm, "pipeline_status", _boom)
    result = rm.get_container_lock_status()  # must not raise

    assert result == {
        "status": "unknown",
        "running": None,
        "scope": "current_container",
        "pid": None,
    }
    # "unknown" must never be silently reported as idle (would hide a
    # possibly-active run) nor as active (would fabricate one), and the
    # exception's own text must never appear anywhere in the result.
    serialized = json.dumps(result)
    assert "filesystem unavailable" not in serialized
    assert "/some/sensitive/path" not in serialized


def test_get_coordinator_summary_never_touches_container_lock(monkeypatch):
    """Proof of independence: computing the coordinator summary must never
    call pipeline_status()/the container lock at all -- these are two
    completely separate signals, not derived from one another."""
    calls: list[None] = []
    monkeypatch.setattr(
        rm, "pipeline_status", lambda: calls.append(None) or {"running": True, "pid": 1}
    )

    session = MagicMock()
    session.execute.return_value.scalar_one.return_value = False
    rm.get_coordinator_summary(session, backend="postgres")

    assert calls == [], "get_coordinator_summary() must never call pipeline_status()"


def test_container_lock_status_never_touches_a_db_session(monkeypatch):
    """The inverse proof: container lock status must never require or
    accept a database Session -- it is computed purely from the local
    filesystem PID lock."""
    monkeypatch.setattr(rm, "pipeline_status", lambda: {"running": False, "pid": 0})
    # get_container_lock_status() takes no arguments at all.
    result = rm.get_container_lock_status()
    assert result["scope"] == "current_container"


# ---------------------------------------------------------------------
# M2B -- honest capability flags: stable shape, never a health signal
# ---------------------------------------------------------------------


def test_capability_flags_are_stable_and_never_signal_health():
    for flag in (
        rm.SURREY_IDENTITY_SCHEDULER_TELEMETRY,
        rm.AI_PIPELINE_TELEMETRY,
    ):
        assert flag == {"available": False, "reason": "run_history_not_persisted"}
        # Must never grow a "status"/"health"/"configured" key that could
        # be confused with an actual health or configuration check.
        assert set(flag.keys()) == {"available", "reason"}


_FORBIDDEN_ENV_SNIPPETS = ("RESEND_API_KEY", "ANTHROPIC_API_KEY", "N8N_", "RAILWAY_")


def test_ops_read_model_never_reads_external_integration_env_vars():
    """The task explicitly rejected a boolean 'configured' check for
    Resend/Anthropic/n8n/Railway -- this module must not read any of
    their env var names, even just to check presence."""
    source = _strip_docstrings_and_comments(inspect.getsource(rm))
    for snippet in _FORBIDDEN_ENV_SNIPPETS:
        assert (
            snippet not in source
        ), f"pipeline/ops_read_model.py must not reference {snippet!r}"


# ---------------------------------------------------------------------
# M2B -- enrichment freshness sources (Google enrichment / CIP / tier):
# fresh, stale, and missing/unknown
# ---------------------------------------------------------------------

_ENRICHMENT_SOURCE_NAMES = ("Google Enrichment", "CIP", "Construction Tier")


def test_enrichment_freshness_sources_are_registered_with_correct_columns():
    by_name = {s.name: s for s in rm.FRESHNESS_SOURCES}
    for name in _ENRICHMENT_SOURCE_NAMES:
        assert name in by_name, f"{name} must be a registered freshness source"

    assert by_name["Google Enrichment"].model is rm.Company
    assert by_name["Google Enrichment"].timestamp_column == "last_enriched_at"
    assert by_name["CIP"].model is rm.Company
    assert by_name["CIP"].timestamp_column == "cip_at"
    assert by_name["Construction Tier"].model is rm.Company
    assert by_name["Construction Tier"].timestamp_column == "construction_tier_at"


@pytest.mark.parametrize("source_name", _ENRICHMENT_SOURCE_NAMES)
def test_enrichment_freshness_source_fresh_is_healthy(source_name):
    source = next(s for s in rm.FRESHNESS_SOURCES if s.name == source_name)
    session = MagicMock()
    session.execute.return_value.scalar_one_or_none.return_value = datetime.now(
        timezone.utc
    ) - timedelta(hours=1)

    result = rm.compute_source_freshness(session, source)

    assert result["status"] == "healthy"
    assert result["latest_record_at"] is not None
    assert result["reason"] is None
    assert result["source_of_truth"] == "companies." + source.timestamp_column


@pytest.mark.parametrize("source_name", _ENRICHMENT_SOURCE_NAMES)
def test_enrichment_freshness_source_stale(source_name):
    source = next(s for s in rm.FRESHNESS_SOURCES if s.name == source_name)
    session = MagicMock()
    session.execute.return_value.scalar_one_or_none.return_value = datetime.now(
        timezone.utc
    ) - timedelta(hours=rm.FRESHNESS_DEGRADED_HOURS + 10)

    result = rm.compute_source_freshness(session, source)

    assert result["status"] == "stale"
    assert result["latest_record_at"] is not None
    assert result["reason"] is None


@pytest.mark.parametrize("source_name", _ENRICHMENT_SOURCE_NAMES)
def test_enrichment_freshness_source_missing_is_unknown(source_name):
    source = next(s for s in rm.FRESHNESS_SOURCES if s.name == source_name)
    session = MagicMock()
    session.execute.return_value.scalar_one_or_none.return_value = None

    result = rm.compute_source_freshness(session, source)

    assert result["status"] == "unknown"
    assert result["latest_record_at"] is None
    assert result["reason"] == "telemetry_not_available"


@pytest.mark.parametrize("source_name", _ENRICHMENT_SOURCE_NAMES)
def test_enrichment_freshness_source_query_failure_degrades_to_unknown(source_name):
    source = next(s for s in rm.FRESHNESS_SOURCES if s.name == source_name)
    session = MagicMock()
    session.execute.side_effect = RuntimeError("companies table unavailable")

    result = rm.compute_source_freshness(session, source)  # must not raise

    assert result["status"] == "unknown"
    assert result["reason"] == "telemetry_not_available"


# ---------------------------------------------------------------------
# No write/DDL side effects (static check)
# ---------------------------------------------------------------------

_FORBIDDEN_SNIPPETS = (
    ".commit(",
    ".add(",
    ".delete(",
    "init_db(",
    "DROP TABLE",
    "CREATE TABLE",
    "INSERT INTO",
    "UPDATE ",
    "DELETE FROM",
)


def _strip_docstrings_and_comments(source: str) -> str:
    """Remove triple-quoted docstrings and '#' comments so prose mentions
    (e.g. this module's own docstring explaining what it does NOT do)
    can't produce a false positive in the write/DDL scan below."""
    no_triple = re.sub(r'""".*?"""', "", source, flags=re.DOTALL)
    return re.sub(r"#.*", "", no_triple)


def test_ops_read_model_module_has_no_write_or_ddl_code():
    source = _strip_docstrings_and_comments(inspect.getsource(rm))
    for snippet in _FORBIDDEN_SNIPPETS:
        assert (
            snippet not in source
        ), f"pipeline/ops_read_model.py must not contain {snippet!r}"


# ---------------------------------------------------------------------
# Real local-Postgres integration tests
# ---------------------------------------------------------------------


@pytest.fixture
def coordinator_db():
    database_url = require_local_test_database()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as probe:
            probe.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")

    with engine.begin() as conn:
        for statement in pipeline_coordinator_migration_statements():
            conn.execute(text(statement))

    def _reset():
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM pipeline_coordinator_steps"))
            conn.execute(text("DELETE FROM pipeline_coordinator_runs"))

    _reset()
    try:
        yield engine
    finally:
        _reset()
        engine.dispose()


def test_coordinator_active_run_ids_reflects_real_active_lease(coordinator_db):
    from db.connection import get_session

    with coordinator_db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO pipeline_coordinator_runs "
                "(run_id, pipeline_scope, status, phase, lease_expires_at) "
                "VALUES (:run_id, 'tender_data', 'active', 'import', NOW() + INTERVAL '1 hour')"
            ),
            {"run_id": "real-active-run"},
        )

    session = get_session()
    try:
        ids = rm.get_coordinator_active_run_ids(session)
        assert "real-active-run" in ids

        summary = rm.get_coordinator_summary(session, backend="postgres")
        assert summary["schema_available"] is True
        assert summary["active_run"]["run_id"] == "real-active-run"
        assert summary["active_run"]["lease_valid"] is True
        assert summary["expired_lease_run"] is None
    finally:
        session.close()


def test_coordinator_active_run_ids_excludes_expired_lease(coordinator_db):
    from db.connection import get_session

    with coordinator_db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO pipeline_coordinator_runs "
                "(run_id, pipeline_scope, status, phase, lease_expires_at) "
                "VALUES (:run_id, 'tender_data', 'active', 'import', NOW() - INTERVAL '1 hour')"
            ),
            {"run_id": "expired-lease-run"},
        )

    session = get_session()
    try:
        ids = rm.get_coordinator_active_run_ids(session)
        assert "expired-lease-run" not in ids

        summary = rm.get_coordinator_summary(session, backend="postgres")
        assert summary["active_run"] is None
        assert summary["expired_lease_run"] is not None
        assert summary["expired_lease_run"]["run_id"] == "expired-lease-run"
    finally:
        session.close()


def test_missing_coordinator_tables_degrades_cleanly(coordinator_db):
    """Drop the coordinator tables entirely and confirm every read-model
    function that touches them degrades to an empty/unavailable result
    instead of raising."""
    from db.connection import get_session

    with coordinator_db.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS pipeline_coordinator_steps"))
        conn.execute(text("DROP TABLE IF EXISTS pipeline_coordinator_runs"))

    session = get_session()
    try:
        assert rm.coordinator_schema_available(session) is False
        assert rm.get_coordinator_active_run_ids(session) == frozenset()
        summary = rm.get_coordinator_summary(session, backend="legacy")
        assert summary == {
            "backend": "legacy",
            "schema_available": False,
            "active_run": None,
            "expired_lease_run": None,
        }
    finally:
        session.close()

    # Restore the schema so other tests in this session aren't affected.
    with coordinator_db.begin() as conn:
        for statement in pipeline_coordinator_migration_statements():
            conn.execute(text(statement))


def test_list_pipeline_runs_and_get_run_detail_real_db():
    from db.connection import get_session
    from db.models import PipelineRun

    database_url = require_local_test_database()
    session = get_session()
    try:
        try:
            session.execute(text("SELECT 1"))
        except Exception:
            pytest.skip("Local Postgres unavailable")

        run_id = "ops-read-model-test-run"
        session.query(PipelineRun).filter(PipelineRun.run_id == run_id).delete()
        session.commit()

        record = PipelineRun(
            run_id=run_id,
            step="scrape-federal",
            status="success",
            counts_json='{"found": 7}',
        )
        session.add(record)
        session.commit()

        try:
            runs = rm.list_pipeline_runs(
                session,
                limit=10,
                job_type="scrape-federal",
                normalized_status_filter=None,
                coordinator_active_run_ids=frozenset(),
            )
            assert any(r["run_id"] == run_id for r in runs)

            detail = rm.get_run_detail(
                session, run_id, coordinator_active_run_ids=frozenset()
            )
            assert detail is not None
            assert detail["run_id"] == run_id
            assert len(detail["steps"]) == 1
            assert detail["steps"][0]["normalized_status"] == "success"
            assert detail["steps"][0]["error_present"] is False

            missing = rm.get_run_detail(
                session, "no-such-run-id-ever", coordinator_active_run_ids=frozenset()
            )
            assert missing is None
        finally:
            session.query(PipelineRun).filter(PipelineRun.run_id == run_id).delete()
            session.commit()
    finally:
        session.close()
