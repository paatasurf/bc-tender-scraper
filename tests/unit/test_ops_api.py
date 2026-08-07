"""Tests for api/ops.py -- the Mission Control M1 read-only ops API's
FastAPI routes: auth guard, GET-only structure, and response shape under
both a working and a degraded database (including get_session() itself
raising, not just check_db_connection() reporting down).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import api.ops as ops_module
from pipeline.scheduler import SURREY_JOB_RUN_JOB_TYPE


def test_all_ops_routes_are_get_only():
    """Task requirement: every /api/ops/* route is GET and nothing else."""
    for route in ops_module.ops_router.routes:
        assert route.methods == {
            "GET"
        }, f"{route.path} must be GET-only, got {route.methods}"


def test_ops_router_prefix_and_route_count():
    paths = sorted(r.path for r in ops_module.ops_router.routes)
    assert paths == [
        "/api/ops/incidents",
        "/api/ops/jobs",
        "/api/ops/jobs/{run_id}",
        "/api/ops/runs",
        "/api/ops/runs/{run_id}",
        "/api/ops/sources",
        "/api/ops/summary",
    ]


def _client():
    from fastapi.testclient import TestClient

    from api.main import app

    return TestClient(app)


_KEY_ENV = {"INTERNAL_API_KEY": "test-secret-key"}
_KEY_HEADER = {"X-Internal-Key": "test-secret-key"}


def test_ops_endpoints_reject_missing_internal_key():
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        for path in (
            "/api/ops/summary",
            "/api/ops/runs",
            "/api/ops/runs/some-run-id",
            "/api/ops/sources",
            "/api/ops/incidents",
            "/api/ops/jobs",
            "/api/ops/jobs/some-run-id",
        ):
            response = client.get(path)
            assert response.status_code == 403, path


def test_ops_endpoints_reject_wrong_internal_key():
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        response = client.get(
            "/api/ops/summary", headers={"X-Internal-Key": "wrong-key"}
        )
        assert response.status_code == 403


def test_ops_endpoints_forbidden_when_internal_key_unconfigured():
    """No INTERNAL_API_KEY configured on the server at all -- must fail
    closed (403), never silently allow access."""
    import os

    client = _client()
    with patch.dict("os.environ", {}, clear=False):
        os.environ.pop("INTERNAL_API_KEY", None)
        response = client.get(
            "/api/ops/summary", headers={"X-Internal-Key": "anything"}
        )
        assert response.status_code == 403


# ---------------------------------------------------------------------
# P1 -- check_db_connection() reports down
# ---------------------------------------------------------------------


def test_ops_summary_never_500s_when_database_is_down():
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=False):
            response = client.get("/api/ops/summary", headers=_KEY_HEADER)
    assert response.status_code == 200
    body = response.json()
    assert body["system"]["database_connected"] is False
    assert body["system"]["api_status"] == "degraded"
    assert body["system"]["coordinator"]["active_run"] is None
    assert body["system"]["coordinator"]["expired_lease_run"] is None
    assert body["system"]["coordinator"]["schema_available"] is False
    # container_lock is independent of database state -- must still be
    # present (not dropped) when the DB is down.
    assert "container_lock" in body["system"]
    assert body["system"]["container_lock"]["scope"] == "current_container"
    # Integrations must be honestly not_connected, never fabricated healthy.
    for integration in body["integrations"]:
        assert integration["status"] == "not_connected"
    assert body["capabilities"] == {
        "incidents_persisted": False,
        "scraper_heartbeats": False,
        "ai_chat_telemetry": False,
        # ENABLE_SURREY_JOB_RUN_TELEMETRY is unset in this test -- flag
        # off takes priority regardless of DB/schema state (M3E-A).
        "surrey_identity_scheduler_telemetry": {
            "available": False,
            "reason": "telemetry_disabled",
        },
        "ai_pipeline_telemetry": {
            "available": False,
            "reason": "run_history_not_persisted",
        },
    }


def test_ops_runs_never_500s_when_database_is_down():
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=False):
            response = client.get("/api/ops/runs", headers=_KEY_HEADER)
    assert response.status_code == 200
    body = response.json()
    assert body["runs"] == []
    assert body["database_connected"] is False


def test_ops_sources_never_500s_when_database_is_down():
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=False):
            response = client.get("/api/ops/sources", headers=_KEY_HEADER)
    assert response.status_code == 200
    body = response.json()
    assert body["database_connected"] is False
    for source in body["sources"]:
        assert source["status"] == "unknown"
        assert source["reason"] == "telemetry_not_available"
        assert "latest_record_at" in source
        assert "last_success_at" not in source


def test_ops_run_detail_returns_503_not_500_when_database_is_down():
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=False):
            response = client.get("/api/ops/runs/some-run-id", headers=_KEY_HEADER)
    assert response.status_code == 503


# ---------------------------------------------------------------------
# P1 -- check_db_connection() says OK, but get_session() itself raises
# (the TOCTOU gap: healthy at check-time, gone by acquisition-time)
# ---------------------------------------------------------------------


def test_ops_summary_never_500s_when_get_session_raises():
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=True):
            with patch(
                "api.ops.get_session", side_effect=RuntimeError("pool exhausted")
            ):
                response = client.get("/api/ops/summary", headers=_KEY_HEADER)
    assert response.status_code == 200
    body = response.json()
    assert body["system"]["coordinator"]["active_run"] is None
    assert body["system"]["coordinator"]["expired_lease_run"] is None
    assert body["system"]["coordinator"]["schema_available"] is False


def test_ops_runs_never_500s_when_get_session_raises():
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=True):
            with patch(
                "api.ops.get_session", side_effect=RuntimeError("pool exhausted")
            ):
                response = client.get("/api/ops/runs", headers=_KEY_HEADER)
    assert response.status_code == 200
    body = response.json()
    assert body["runs"] == []
    assert body["database_connected"] is False


def test_ops_sources_never_500s_when_get_session_raises():
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=True):
            with patch(
                "api.ops.get_session", side_effect=RuntimeError("pool exhausted")
            ):
                response = client.get("/api/ops/sources", headers=_KEY_HEADER)
    assert response.status_code == 200
    body = response.json()
    assert body["database_connected"] is False
    for source in body["sources"]:
        assert source["status"] == "unknown"


def test_ops_run_detail_returns_503_when_get_session_raises():
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=True):
            with patch(
                "api.ops.get_session", side_effect=RuntimeError("pool exhausted")
            ):
                response = client.get("/api/ops/runs/some-run-id", headers=_KEY_HEADER)
    assert response.status_code == 503


def test_ops_summary_never_500s_when_query_raises_after_session_acquired():
    """get_session() succeeds, but the actual query inside it blows up --
    the session must still be closed, and the response still degrades
    instead of 500ing."""
    client = _client()
    fake_session = object()  # any object; .close must be called on it if it were real
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=True):
            with patch("api.ops.get_session", return_value=fake_session):
                with patch(
                    "api.ops.get_coordinator_summary",
                    side_effect=RuntimeError("query blew up"),
                ):
                    response = client.get("/api/ops/summary", headers=_KEY_HEADER)
    assert response.status_code == 200
    body = response.json()
    assert body["system"]["coordinator"]["active_run"] is None


def test_call_with_session_closes_session_only_if_acquired():
    """Unit-level proof of the acquisition-vs-close asymmetry: if
    get_session() raises, there is nothing to close (and _call_with_session
    must not blow up trying)."""
    with patch("api.ops.get_session", side_effect=RuntimeError("down")):
        ok, result = ops_module._call_with_session(lambda session: session)
    assert ok is False
    assert result is None


def test_call_with_session_closes_a_real_session_even_on_query_failure():
    from unittest.mock import MagicMock

    session = MagicMock()
    with patch("api.ops.get_session", return_value=session):
        ok, result = ops_module._call_with_session(
            lambda s: (_ for _ in ()).throw(RuntimeError("boom"))
        )
    assert ok is False
    assert result is None
    session.close.assert_called_once()


def test_call_with_session_closes_session_on_success_too():
    from unittest.mock import MagicMock

    session = MagicMock()
    with patch("api.ops.get_session", return_value=session):
        ok, result = ops_module._call_with_session(lambda s: "value")
    assert ok is True
    assert result == "value"
    session.close.assert_called_once()


# ---------------------------------------------------------------------
# 404 / capability placeholder / contract shape
# ---------------------------------------------------------------------


def test_ops_run_detail_returns_404_for_unknown_run_id():
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=True):
            with patch("api.ops.get_session") as mock_get_session:
                mock_get_session.return_value = mock_get_session.return_value
                with patch(
                    "api.ops.get_coordinator_active_run_ids", return_value=frozenset()
                ):
                    with patch("api.ops.get_run_detail", return_value=None):
                        response = client.get(
                            "/api/ops/runs/does-not-exist", headers=_KEY_HEADER
                        )
    assert response.status_code == 404


def test_ops_incidents_always_returns_capability_placeholder():
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        response = client.get("/api/ops/incidents", headers=_KEY_HEADER)
    assert response.status_code == 200
    body = response.json()
    assert body["incidents"] == []
    assert body["capability"]["available"] is False
    assert "reason" in body["capability"]


def test_ops_summary_contract_shape_with_working_database():
    """Confirms the exact top-level keys the M1 task's contract specifies,
    with the database mocked as healthy so this test needs no real DB."""
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=True):
            with patch("api.ops.get_session") as mock_get_session:
                mock_get_session.return_value = mock_get_session.return_value
                with patch(
                    "api.ops.get_coordinator_summary",
                    return_value={
                        "backend": "legacy",
                        "schema_available": False,
                        "active_run": None,
                        "expired_lease_run": None,
                    },
                ):
                    response = client.get("/api/ops/summary", headers=_KEY_HEADER)
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {
        "generated_at",
        "system",
        "integrations",
        "capabilities",
        "job_types",
    }
    assert set(body["system"].keys()) == {
        "api_status",
        "database_connected",
        "scheduler",
        "coordinator",
        "container_lock",
    }
    assert set(body["system"]["coordinator"].keys()) == {
        "backend",
        "schema_available",
        "active_run",
        "expired_lease_run",
    }
    assert set(body["system"]["container_lock"].keys()) == {
        "status",
        "running",
        "scope",
        "pid",
    }
    assert body["system"]["container_lock"]["scope"] == "current_container"
    assert set(body["capabilities"].keys()) == {
        "incidents_persisted",
        "scraper_heartbeats",
        "ai_chat_telemetry",
        "surrey_identity_scheduler_telemetry",
        "ai_pipeline_telemetry",
    }
    assert body["system"]["coordinator"]["backend"] == "legacy"
    integration_names = {i["name"] for i in body["integrations"]}
    assert integration_names == {
        "Railway",
        "n8n",
        "Clerk",
        "Vercel",
        "Resend",
        "AI Assistant",
    }


def test_ops_sources_includes_merx_open_separately_from_federal():
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=False):
            response = client.get("/api/ops/sources", headers=_KEY_HEADER)
    body = response.json()
    names = {s["name"] for s in body["sources"]}
    assert "Federal" in names
    assert "MERX Open" in names
    assert "MERX Architecture" in names


# ---------------------------------------------------------------------
# M2B -- container_lock, coordinator error/success, honest capabilities
# ---------------------------------------------------------------------


def test_ops_summary_container_lock_reflects_active():
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=True):
            with patch("api.ops.get_session") as mock_get_session:
                mock_get_session.return_value = mock_get_session.return_value
                with patch(
                    "api.ops.get_coordinator_summary",
                    return_value={
                        "backend": "postgres",
                        "schema_available": True,
                        "active_run": None,
                        "expired_lease_run": None,
                    },
                ):
                    with patch(
                        "api.ops.get_container_lock_status",
                        return_value={
                            "status": "active",
                            "running": True,
                            "scope": "current_container",
                            "pid": 4242,
                        },
                    ):
                        response = client.get("/api/ops/summary", headers=_KEY_HEADER)
    body = response.json()
    assert body["system"]["container_lock"] == {
        "status": "active",
        "running": True,
        "scope": "current_container",
        "pid": 4242,
    }
    # Independence: a running container lock must never leak into or
    # substitute for the coordinator's own active_run.
    assert body["system"]["coordinator"]["active_run"] is None


def test_ops_summary_container_lock_reflects_idle():
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=True):
            with patch(
                "api.ops.get_container_lock_status",
                return_value={
                    "status": "idle",
                    "running": False,
                    "scope": "current_container",
                    "pid": None,
                },
            ):
                response = client.get("/api/ops/summary", headers=_KEY_HEADER)
    body = response.json()
    assert body["system"]["container_lock"] == {
        "status": "idle",
        "running": False,
        "scope": "current_container",
        "pid": None,
    }


def test_ops_summary_container_lock_reflects_unknown_on_read_failure():
    """A container_lock read failure must surface as an honest
    status="unknown"/running=None through the route, not be silently
    collapsed into idle (running=False)."""
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=True):
            with patch(
                "api.ops.get_container_lock_status",
                return_value={
                    "status": "unknown",
                    "running": None,
                    "scope": "current_container",
                    "pid": None,
                },
            ):
                response = client.get("/api/ops/summary", headers=_KEY_HEADER)
    body = response.json()
    assert body["system"]["container_lock"]["status"] == "unknown"
    assert body["system"]["container_lock"]["running"] is None


def test_ops_summary_container_lock_present_even_when_database_down():
    """container_lock has nothing to do with the database -- it must not
    be dropped or blanked out just because check_db_connection() is
    False."""
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=False):
            with patch(
                "api.ops.get_container_lock_status",
                return_value={
                    "status": "active",
                    "running": True,
                    "scope": "current_container",
                    "pid": 7,
                },
            ):
                response = client.get("/api/ops/summary", headers=_KEY_HEADER)
    body = response.json()
    assert body["system"]["database_connected"] is False
    assert body["system"]["container_lock"] == {
        "status": "active",
        "running": True,
        "scope": "current_container",
        "pid": 7,
    }


def test_ops_summary_capabilities_are_stable_shape_not_health():
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=True):
            with patch("api.ops.get_session") as mock_get_session:
                mock_get_session.return_value = mock_get_session.return_value
                with patch(
                    "api.ops.get_coordinator_summary",
                    return_value={
                        "backend": "postgres",
                        "schema_available": True,
                        "active_run": None,
                        "expired_lease_run": None,
                    },
                ):
                    response = client.get("/api/ops/summary", headers=_KEY_HEADER)
    body = response.json()
    # ENABLE_SURREY_JOB_RUN_TELEMETRY is unset in this test -- flag off
    # takes priority (M3E-A dynamic capability).
    assert body["capabilities"]["surrey_identity_scheduler_telemetry"] == {
        "available": False,
        "reason": "telemetry_disabled",
    }
    assert body["capabilities"]["ai_pipeline_telemetry"] == {
        "available": False,
        "reason": "run_history_not_persisted",
    }


def test_ops_summary_coordinator_active_run_never_leaks_raw_error_via_api():
    """Route-level proof that api/ops.py passes the coordinator payload
    through untouched -- the raw-error-never-leaks guarantee itself is
    proven in tests/unit/test_ops_read_model.py; this confirms the route
    doesn't reintroduce a raw error field on the way out."""
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=True):
            with patch("api.ops.get_session") as mock_get_session:
                mock_get_session.return_value = mock_get_session.return_value
                with patch(
                    "api.ops.get_coordinator_summary",
                    return_value={
                        "backend": "postgres",
                        "schema_available": True,
                        "active_run": {
                            "run_id": "r1",
                            "phase": "import",
                            "lease_valid": True,
                            "lease_expires_at": None,
                            "started_at": None,
                            "success": None,
                            "stale_reclaimed": False,
                            "error_present": False,
                            "error_summary": None,
                        },
                        "expired_lease_run": None,
                    },
                ):
                    response = client.get("/api/ops/summary", headers=_KEY_HEADER)
    body = response.json()
    active_run = body["system"]["coordinator"]["active_run"]
    assert "error" not in active_run
    assert active_run["success"] is None
    assert active_run["stale_reclaimed"] is False
    assert active_run["error_present"] is False


# ---------------------------------------------------------------------
# M3E-A -- GET /api/ops/jobs / GET /api/ops/jobs/{run_id}
# ---------------------------------------------------------------------


def test_ops_jobs_routes_are_get_only_and_read_only_by_construction():
    """Explicit, task-required structural proof for the two new M3E-A
    routes specifically (beyond the generic ops_router-wide GET-only check
    at the top of this file): no body, no write method."""
    jobs_routes = [
        r for r in ops_module.ops_router.routes if r.path.startswith("/api/ops/jobs")
    ]
    assert len(jobs_routes) == 2
    for route in jobs_routes:
        assert route.methods == {"GET"}


def test_ops_jobs_rejects_invalid_status_with_422_not_500():
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        response = client.get(
            "/api/ops/jobs", params={"status": "running"}, headers=_KEY_HEADER
        )
    assert response.status_code == 422


def test_ops_jobs_never_500s_when_database_is_down():
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=False):
            response = client.get("/api/ops/jobs", headers=_KEY_HEADER)
    assert response.status_code == 200
    body = response.json()
    assert body["jobs"] == []
    assert body["count"] == 0
    assert body["database_connected"] is False
    assert body["schema_available"] is False


def test_ops_jobs_never_500s_when_get_session_raises():
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=True):
            with patch(
                "api.ops.get_session", side_effect=RuntimeError("pool exhausted")
            ):
                response = client.get("/api/ops/jobs", headers=_KEY_HEADER)
    assert response.status_code == 200
    body = response.json()
    assert body["jobs"] == []
    assert body["database_connected"] is False
    assert body["schema_available"] is False


def test_ops_jobs_degrades_to_honest_empty_payload_when_schema_unavailable():
    """migration 033 not applied on this environment -- an honest 200,
    empty list, schema_available=False, never a 503/500 (task's chosen
    graceful-degradation contract for the list endpoint)."""
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=True):
            with patch("api.ops.get_session") as mock_get_session:
                mock_get_session.return_value = mock_get_session.return_value
                with patch("api.ops.ops_job_run_schema_available", return_value=False):
                    response = client.get("/api/ops/jobs", headers=_KEY_HEADER)
    assert response.status_code == 200
    body = response.json()
    assert body["jobs"] == []
    assert body["count"] == 0
    assert body["database_connected"] is True
    assert body["schema_available"] is False


def _sample_job_payload(run_id: str = "r-1", **overrides) -> dict:
    payload = {
        "run_id": run_id,
        "job_type": "surrey_identity_scheduler",
        "source": "surrey",
        "trigger": "scheduler",
        "normalized_status": "success",
        "started_at": "2026-08-07T12:30:00+00:00",
        "heartbeat_at": "2026-08-07T12:30:08+00:00",
        "finished_at": "2026-08-07T12:30:08+00:00",
        "lease_expires_at": "2026-08-07T13:00:08+00:00",
        "counts": {
            "source_rows": 1282,
            "inserted": 6,
            "updated": 1276,
            "error_count": 0,
        },
        "error_present": False,
        "error_summary": None,
    }
    payload.update(overrides)
    return payload


def test_ops_jobs_returns_jobs_with_working_database():
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=True):
            with patch("api.ops.get_session") as mock_get_session:
                mock_get_session.return_value = mock_get_session.return_value
                with patch("api.ops.ops_job_run_schema_available", return_value=True):
                    with patch(
                        "api.ops.list_ops_job_runs",
                        return_value=[_sample_job_payload()],
                    ):
                        response = client.get("/api/ops/jobs", headers=_KEY_HEADER)
    assert response.status_code == 200
    body = response.json()
    assert body["database_connected"] is True
    assert body["schema_available"] is True
    assert body["count"] == 1
    assert body["jobs"][0]["run_id"] == "r-1"
    assert "plan_digest" not in body["jobs"][0]
    assert "result_digest" not in body["jobs"][0]


def test_ops_jobs_limit_is_capped_at_100():
    client = _client()
    captured = {}

    def _fake_list(session, *, job_type, status, limit):
        captured["limit"] = limit
        return []

    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=True):
            with patch("api.ops.get_session") as mock_get_session:
                mock_get_session.return_value = mock_get_session.return_value
                with patch("api.ops.ops_job_run_schema_available", return_value=True):
                    with patch("api.ops.list_ops_job_runs", side_effect=_fake_list):
                        response = client.get(
                            "/api/ops/jobs", params={"limit": 5000}, headers=_KEY_HEADER
                        )
    assert response.status_code == 200
    assert captured["limit"] == 100


def test_ops_jobs_default_limit_is_50():
    client = _client()
    captured = {}

    def _fake_list(session, *, job_type, status, limit):
        captured["limit"] = limit
        return []

    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=True):
            with patch("api.ops.get_session") as mock_get_session:
                mock_get_session.return_value = mock_get_session.return_value
                with patch("api.ops.ops_job_run_schema_available", return_value=True):
                    with patch("api.ops.list_ops_job_runs", side_effect=_fake_list):
                        response = client.get("/api/ops/jobs", headers=_KEY_HEADER)
    assert response.status_code == 200
    assert captured["limit"] == 50


def test_ops_jobs_passes_job_type_and_status_filters_through():
    client = _client()
    captured = {}

    def _fake_list(session, *, job_type, status, limit):
        captured["job_type"] = job_type
        captured["status"] = status
        return []

    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=True):
            with patch("api.ops.get_session") as mock_get_session:
                mock_get_session.return_value = mock_get_session.return_value
                with patch("api.ops.ops_job_run_schema_available", return_value=True):
                    with patch("api.ops.list_ops_job_runs", side_effect=_fake_list):
                        response = client.get(
                            "/api/ops/jobs",
                            params={
                                "job_type": "surrey_identity_scheduler",
                                "status": "active",
                            },
                            headers=_KEY_HEADER,
                        )
    assert response.status_code == 200
    assert captured["job_type"] == "surrey_identity_scheduler"
    assert captured["status"] == "active"


def test_ops_job_detail_returns_503_when_database_is_down():
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=False):
            response = client.get("/api/ops/jobs/some-run-id", headers=_KEY_HEADER)
    assert response.status_code == 503


def test_ops_job_detail_returns_503_when_get_session_raises():
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=True):
            with patch(
                "api.ops.get_session", side_effect=RuntimeError("pool exhausted")
            ):
                response = client.get("/api/ops/jobs/some-run-id", headers=_KEY_HEADER)
    assert response.status_code == 503


def test_ops_job_detail_returns_503_not_404_when_schema_unavailable():
    """A missing schema means the lookup was never actually performed --
    503 (honestly "couldn't tell"), never a 404 that would falsely claim
    "checked, not found" (task's chosen contract for the detail
    endpoint, distinct from the list endpoint's graceful-empty 200)."""
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=True):
            with patch("api.ops.get_session") as mock_get_session:
                mock_get_session.return_value = mock_get_session.return_value
                with patch("api.ops.ops_job_run_schema_available", return_value=False):
                    response = client.get(
                        "/api/ops/jobs/some-run-id", headers=_KEY_HEADER
                    )
    assert response.status_code == 503


def test_ops_job_detail_returns_404_for_unknown_run_id():
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=True):
            with patch("api.ops.get_session") as mock_get_session:
                mock_get_session.return_value = mock_get_session.return_value
                with patch("api.ops.ops_job_run_schema_available", return_value=True):
                    with patch("api.ops.get_ops_job_run_detail", return_value=None):
                        response = client.get(
                            "/api/ops/jobs/does-not-exist", headers=_KEY_HEADER
                        )
    assert response.status_code == 404


def test_ops_job_detail_returns_200_with_working_database():
    detail_payload = {
        **_sample_job_payload(),
        "events": [
            {
                "event_type": "started",
                "step": None,
                "counts_delta": None,
                "occurred_at": "2026-08-07T12:30:00+00:00",
            },
            {
                "event_type": "step_completed",
                "step": "plan",
                "counts_delta": None,
                "occurred_at": "2026-08-07T12:30:02+00:00",
            },
            {
                "event_type": "finished",
                "step": None,
                "counts_delta": None,
                "occurred_at": "2026-08-07T12:30:08+00:00",
            },
        ],
    }
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=True):
            with patch("api.ops.get_session") as mock_get_session:
                mock_get_session.return_value = mock_get_session.return_value
                with patch("api.ops.ops_job_run_schema_available", return_value=True):
                    with patch(
                        "api.ops.get_ops_job_run_detail", return_value=detail_payload
                    ):
                        response = client.get("/api/ops/jobs/r-1", headers=_KEY_HEADER)
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == "r-1"
    assert [e["event_type"] for e in body["events"]] == [
        "started",
        "step_completed",
        "finished",
    ]
    assert "generated_at" in body


# ---------------------------------------------------------------------
# M3E-A -- GET /api/ops/summary's new job_types block
# ---------------------------------------------------------------------


def test_ops_summary_job_types_present_even_when_database_down():
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=False):
            response = client.get("/api/ops/summary", headers=_KEY_HEADER)
    body = response.json()
    assert body["job_types"] == {
        SURREY_JOB_RUN_JOB_TYPE: {
            "last_run_at": None,
            "last_status": None,
            "counts": None,
        }
    }


def test_ops_summary_job_types_all_null_when_schema_unavailable():
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=True):
            with patch("api.ops.get_session") as mock_get_session:
                mock_get_session.return_value = mock_get_session.return_value
                with patch(
                    "api.ops.get_coordinator_summary",
                    return_value={
                        "backend": "legacy",
                        "schema_available": False,
                        "active_run": None,
                        "expired_lease_run": None,
                    },
                ):
                    with patch(
                        "api.ops.ops_job_run_schema_available", return_value=False
                    ):
                        response = client.get("/api/ops/summary", headers=_KEY_HEADER)
    body = response.json()
    assert body["job_types"][SURREY_JOB_RUN_JOB_TYPE] == {
        "last_run_at": None,
        "last_status": None,
        "counts": None,
    }


def test_ops_summary_job_types_reflects_a_real_last_run_never_healthy_when_absent():
    client = _client()
    real_summary = {
        "last_run_at": "2026-08-07T12:30:00+00:00",
        "last_status": "success",
        "counts": {
            "source_rows": 1282,
            "inserted": 6,
            "updated": 1276,
            "error_count": 0,
        },
    }
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=True):
            with patch("api.ops.get_session") as mock_get_session:
                mock_get_session.return_value = mock_get_session.return_value
                with patch(
                    "api.ops.get_coordinator_summary",
                    return_value={
                        "backend": "legacy",
                        "schema_available": True,
                        "active_run": None,
                        "expired_lease_run": None,
                    },
                ):
                    with patch(
                        "api.ops.ops_job_run_schema_available", return_value=True
                    ):
                        with patch(
                            "api.ops.get_job_type_summary", return_value=real_summary
                        ):
                            response = client.get(
                                "/api/ops/summary", headers=_KEY_HEADER
                            )
    body = response.json()
    assert body["job_types"][SURREY_JOB_RUN_JOB_TYPE] == real_summary


def test_ops_summary_job_types_never_500s_when_query_raises():
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=True):
            with patch("api.ops.get_session") as mock_get_session:
                mock_get_session.return_value = mock_get_session.return_value
                with patch(
                    "api.ops.get_coordinator_summary",
                    return_value={
                        "backend": "legacy",
                        "schema_available": False,
                        "active_run": None,
                        "expired_lease_run": None,
                    },
                ):
                    with patch(
                        "api.ops.ops_job_run_schema_available",
                        side_effect=RuntimeError("boom"),
                    ):
                        response = client.get("/api/ops/summary", headers=_KEY_HEADER)
    assert response.status_code == 200
    body = response.json()
    assert body["job_types"][SURREY_JOB_RUN_JOB_TYPE] == {
        "last_run_at": None,
        "last_status": None,
        "counts": None,
    }


# ---------------------------------------------------------------------
# M3E-A follow-up -- dynamic capabilities.surrey_identity_scheduler_telemetry
# (fixed by request after PR #126 review: the old static
# SURREY_IDENTITY_SCHEDULER_TELEMETRY constant was still reporting
# available=False even after M3C gave Surrey a real writer)
# ---------------------------------------------------------------------


def test_ops_summary_surrey_capability_telemetry_disabled_when_flag_off():
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=True):
            with patch("api.ops.get_session") as mock_get_session:
                mock_get_session.return_value = mock_get_session.return_value
                with patch(
                    "api.ops.get_coordinator_summary",
                    return_value={
                        "backend": "legacy",
                        "schema_available": True,
                        "active_run": None,
                        "expired_lease_run": None,
                    },
                ):
                    # Flag off must win even though the schema is available.
                    with patch(
                        "api.ops.ops_job_run_schema_available", return_value=True
                    ):
                        with patch(
                            "api.ops.surrey_job_run_telemetry_enabled",
                            return_value=False,
                        ):
                            response = client.get(
                                "/api/ops/summary", headers=_KEY_HEADER
                            )
    body = response.json()
    assert body["capabilities"]["surrey_identity_scheduler_telemetry"] == {
        "available": False,
        "reason": "telemetry_disabled",
    }


def test_ops_summary_surrey_capability_schema_unavailable_when_flag_on_but_schema_missing():
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=True):
            with patch("api.ops.get_session") as mock_get_session:
                mock_get_session.return_value = mock_get_session.return_value
                with patch(
                    "api.ops.get_coordinator_summary",
                    return_value={
                        "backend": "legacy",
                        "schema_available": False,
                        "active_run": None,
                        "expired_lease_run": None,
                    },
                ):
                    with patch(
                        "api.ops.ops_job_run_schema_available", return_value=False
                    ):
                        with patch(
                            "api.ops.surrey_job_run_telemetry_enabled",
                            return_value=True,
                        ):
                            response = client.get(
                                "/api/ops/summary", headers=_KEY_HEADER
                            )
    body = response.json()
    assert body["capabilities"]["surrey_identity_scheduler_telemetry"] == {
        "available": False,
        "reason": "schema_unavailable",
    }


def test_ops_summary_surrey_capability_schema_unavailable_when_flag_on_but_database_down():
    """Flag on, but the database itself is down -- the schema check never
    ran, so this must degrade to schema_unavailable (the conservative,
    honest "couldn't confirm" reading), never a false available=True."""
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=False):
            with patch("api.ops.surrey_job_run_telemetry_enabled", return_value=True):
                response = client.get("/api/ops/summary", headers=_KEY_HEADER)
    body = response.json()
    assert body["capabilities"]["surrey_identity_scheduler_telemetry"] == {
        "available": False,
        "reason": "schema_unavailable",
    }


def test_ops_summary_surrey_capability_available_when_flag_on_and_schema_ready():
    client = _client()
    with patch.dict("os.environ", _KEY_ENV, clear=False):
        with patch("api.ops.check_db_connection", return_value=True):
            with patch("api.ops.get_session") as mock_get_session:
                mock_get_session.return_value = mock_get_session.return_value
                with patch(
                    "api.ops.get_coordinator_summary",
                    return_value={
                        "backend": "legacy",
                        "schema_available": True,
                        "active_run": None,
                        "expired_lease_run": None,
                    },
                ):
                    with patch(
                        "api.ops.ops_job_run_schema_available", return_value=True
                    ):
                        with patch(
                            "api.ops.get_job_type_summary",
                            return_value={
                                "last_run_at": "2026-08-07T12:30:00+00:00",
                                "last_status": "success",
                                "counts": {"source_rows": 1},
                            },
                        ):
                            with patch(
                                "api.ops.surrey_job_run_telemetry_enabled",
                                return_value=True,
                            ):
                                response = client.get(
                                    "/api/ops/summary", headers=_KEY_HEADER
                                )
    body = response.json()
    assert body["capabilities"]["surrey_identity_scheduler_telemetry"] == {
        "available": True,
        "reason": None,
    }


def test_ops_summary_surrey_capability_never_leaks_raw_errors_or_other_env_vars():
    """surrey_job_run_telemetry_enabled() is the ONLY env-var read for this
    capability -- proven here by seeding an unrelated secret-like env var
    and confirming it never appears anywhere in the response body, on top
    of the general "no raw error text" guarantee."""
    client = _client()
    env = {
        **_KEY_ENV,
        "SOME_OTHER_SECRET": "sk_live_should_never_appear_anywhere",
    }
    with patch.dict("os.environ", env, clear=False):
        with patch("api.ops.check_db_connection", return_value=True):
            with patch("api.ops.get_session") as mock_get_session:
                mock_get_session.return_value = mock_get_session.return_value
                with patch(
                    "api.ops.get_coordinator_summary",
                    return_value={
                        "backend": "legacy",
                        "schema_available": True,
                        "active_run": None,
                        "expired_lease_run": None,
                    },
                ):
                    with patch(
                        "api.ops.ops_job_run_schema_available",
                        side_effect=RuntimeError(
                            "relation ops_job_runs does not exist: secret-looking-text"
                        ),
                    ):
                        response = client.get("/api/ops/summary", headers=_KEY_HEADER)
    assert response.status_code == 200
    body = response.json()
    serialized = str(body)
    assert "sk_live_should_never_appear_anywhere" not in serialized
    assert "secret-looking-text" not in serialized
    assert body["capabilities"]["surrey_identity_scheduler_telemetry"]["reason"] in {
        "telemetry_disabled",
        "schema_unavailable",
    }
