"""Tests for api/ops.py -- the Mission Control M1 read-only ops API's
FastAPI routes: auth guard, GET-only structure, and response shape under
both a working and a degraded database (including get_session() itself
raising, not just check_db_connection() reporting down).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import api.ops as ops_module


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
    # Integrations must be honestly not_connected, never fabricated healthy.
    for integration in body["integrations"]:
        assert integration["status"] == "not_connected"
    assert body["capabilities"] == {
        "incidents_persisted": False,
        "scraper_heartbeats": False,
        "ai_chat_telemetry": False,
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
    }
    assert set(body["system"].keys()) == {
        "api_status",
        "database_connected",
        "scheduler",
        "coordinator",
    }
    assert set(body["system"]["coordinator"].keys()) == {
        "backend",
        "schema_available",
        "active_run",
        "expired_lease_run",
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
