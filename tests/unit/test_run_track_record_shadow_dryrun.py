"""Tests for scripts/run_track_record_shadow_dryrun.py (PR-G3.3a).

Sections:
  1. Structural -- no DB: parser never has --apply/--allow-production,
     guard_readonly_db_from_args is used, single .connect() call.
  2. CLI-level behavioral -- no DB: --use-production routing through
     guard_readonly_db_from_args (short-circuited via monkeypatch before
     any real connection is attempted).
  3. DB-backed (local Postgres only) -- exercises run_dry_run() directly
     against a real Engine. Every scenario uses company_ids=[] (a
     legitimate, always-safe empty selection -- PR-G3.2's own contract)
     or a genuinely read-only dry_run=True call against whatever real
     companies already exist locally, so none of these tests ever need
     to write or clean up test data.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text

from tests.db_test_safety import require_local_test_database

MODULE_FILE = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "run_track_record_shadow_dryrun.py"
)
CLASSIFICATION_FILE = MODULE_FILE.parent / "CLASSIFICATION.md"


# ===================================================================
# 1. Structural -- no DB
# ===================================================================


def test_module_never_imports_add_production_safety_args_or_class_c_guard():
    source = MODULE_FILE.read_text(encoding="utf-8")
    assert "add_production_safety_args" not in source
    assert "guard_destructive_db_from_args" not in source
    assert '"--apply"' not in source
    assert "'--apply'" not in source
    assert '"--allow-production"' not in source
    assert "'--allow-production'" not in source


def test_module_uses_guard_readonly_db_from_args():
    source = MODULE_FILE.read_text(encoding="utf-8")
    assert "guard_readonly_db_from_args" in source


def test_module_opens_exactly_one_connection():
    source = MODULE_FILE.read_text(encoding="utf-8")
    assert source.count(".connect()") == 1


def test_module_issues_read_only_isolation_statement():
    source = MODULE_FILE.read_text(encoding="utf-8")
    assert "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY" in source


def test_module_calls_backfill_with_dry_run_true_literal():
    source = MODULE_FILE.read_text(encoding="utf-8")
    assert "dry_run=True" in source


def test_classification_md_documents_the_new_script():
    classification = CLASSIFICATION_FILE.read_text(encoding="utf-8")
    assert "run_track_record_shadow_dryrun.py" in classification


# ===================================================================
# 2. CLI-level behavioral -- no DB (guard short-circuits before any
#    real connection is attempted)
# ===================================================================


def test_cli_rejects_apply_flag_as_unrecognized(monkeypatch):
    import scripts.run_track_record_shadow_dryrun as script_mod

    monkeypatch.setattr(sys, "argv", ["run_track_record_shadow_dryrun.py", "--apply"])
    with pytest.raises(SystemExit):
        script_mod.main()


def test_cli_rejects_allow_production_flag_as_unrecognized(monkeypatch):
    import scripts.run_track_record_shadow_dryrun as script_mod

    monkeypatch.setattr(
        sys, "argv", ["run_track_record_shadow_dryrun.py", "--allow-production"]
    )
    with pytest.raises(SystemExit):
        script_mod.main()


def test_use_production_flag_routes_through_guard_readonly(monkeypatch):
    import scripts.run_track_record_shadow_dryrun as script_mod

    captured = {}

    def fake_guard(args, *, script_name):
        captured["use_production"] = getattr(args, "use_production", None)
        captured["script_name"] = script_name
        raise SystemExit(0)  # short-circuit before any real DB connection

    monkeypatch.setattr(script_mod, "guard_readonly_db_from_args", fake_guard)
    monkeypatch.setattr(
        sys, "argv", ["run_track_record_shadow_dryrun.py", "--use-production"]
    )

    with pytest.raises(SystemExit):
        script_mod.main()
    assert captured["use_production"] is True
    assert captured["script_name"] == "run_track_record_shadow_dryrun.py"


def test_no_use_production_flag_defaults_to_local(monkeypatch):
    import scripts.run_track_record_shadow_dryrun as script_mod

    captured = {}

    def fake_guard(args, *, script_name):
        captured["use_production"] = getattr(args, "use_production", None)
        raise SystemExit(0)

    monkeypatch.setattr(script_mod, "guard_readonly_db_from_args", fake_guard)
    monkeypatch.setattr(sys, "argv", ["run_track_record_shadow_dryrun.py"])

    with pytest.raises(SystemExit):
        script_mod.main()
    assert captured["use_production"] is False


def test_invalid_reference_date_causes_parser_error(monkeypatch):
    import scripts.run_track_record_shadow_dryrun as script_mod

    monkeypatch.setattr(
        sys,
        "argv",
        ["run_track_record_shadow_dryrun.py", "--reference-date", "not-a-date"],
    )
    with pytest.raises(SystemExit):
        script_mod.main()


# ===================================================================
# 3. DB-backed -- local Postgres only
# ===================================================================


@pytest.fixture()
def local_engine():
    database_url = require_local_test_database()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as probe:
            probe.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")
    yield engine
    engine.dispose()


def test_read_only_transaction_rejects_a_write_attempt(local_engine):
    """Independent of run_dry_run's own internals -- proves the isolation
    mechanism itself: once REPEATABLE READ, READ ONLY has been set on a
    connection, Postgres refuses any DML on that same connection, even a
    zero-row-affecting one (WHERE false), before any row is touched."""
    conn = local_engine.connect()
    trans = conn.begin()
    try:
        conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        with pytest.raises(Exception):
            conn.execute(
                text("UPDATE companies SET total_projects = total_projects WHERE false")
            )
    finally:
        trans.rollback()
        conn.close()


def test_run_dry_run_writes_valid_artifact_for_empty_selection(local_engine, tmp_path):
    import scripts.run_track_record_shadow_dryrun as script_mod

    artifact_path = tmp_path / "artifact.json"
    artifact = script_mod.run_dry_run(
        local_engine,
        artifact_path=artifact_path,
        company_ids=[],
        sample_size=None,
        reference_date=None,
        force=False,
    )
    assert artifact_path.is_file()
    on_disk = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert on_disk == artifact
    assert artifact["selected"] == 0
    assert artifact["dry_run"] is True
    assert artifact["eligibility_digest"] == hashlib.sha256(b"").hexdigest()
    assert len(artifact["eligibility_digest"]) == 64


def test_single_connection_checked_out(local_engine, tmp_path):
    import scripts.run_track_record_shadow_dryrun as script_mod

    checkouts: list[int] = []

    def on_checkout(dbapi_conn, connection_record, connection_proxy):
        checkouts.append(1)

    event.listen(local_engine, "checkout", on_checkout)
    try:
        script_mod.run_dry_run(
            local_engine,
            artifact_path=tmp_path / "a.json",
            company_ids=[],
            sample_size=None,
            reference_date=None,
            force=False,
        )
    finally:
        event.remove(local_engine, "checkout", on_checkout)
    assert len(checkouts) == 1


def test_read_only_statement_issued_first_on_the_connection(local_engine, tmp_path):
    import scripts.run_track_record_shadow_dryrun as script_mod

    captured: list[str] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        captured.append(statement)

    event.listen(local_engine, "before_cursor_execute", capture)
    try:
        script_mod.run_dry_run(
            local_engine,
            artifact_path=tmp_path / "a.json",
            company_ids=[],
            sample_size=None,
            reference_date=None,
            force=False,
        )
    finally:
        event.remove(local_engine, "before_cursor_execute", capture)
    assert captured, "expected at least one statement to have been executed"
    assert "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY" in captured[0]


def test_rollback_guaranteed_on_success_never_commits(local_engine, tmp_path):
    import scripts.run_track_record_shadow_dryrun as script_mod

    rollbacks: list[int] = []
    commits: list[int] = []

    def on_rollback(conn):
        rollbacks.append(1)

    def on_commit(conn):
        commits.append(1)

    event.listen(local_engine, "rollback", on_rollback)
    event.listen(local_engine, "commit", on_commit)
    try:
        script_mod.run_dry_run(
            local_engine,
            artifact_path=tmp_path / "a.json",
            company_ids=[],
            sample_size=None,
            reference_date=None,
            force=False,
        )
    finally:
        event.remove(local_engine, "rollback", on_rollback)
        event.remove(local_engine, "commit", on_commit)
    assert len(rollbacks) == 1
    assert len(commits) == 0


def test_rollback_guaranteed_on_exception_never_commits(
    local_engine, tmp_path, monkeypatch
):
    import scripts.run_track_record_shadow_dryrun as script_mod

    def boom(session, **kwargs):
        raise RuntimeError("simulated backfill failure")

    monkeypatch.setattr(script_mod, "backfill_company_track_records", boom)

    rollbacks: list[int] = []
    commits: list[int] = []

    def on_rollback(conn):
        rollbacks.append(1)

    def on_commit(conn):
        commits.append(1)

    event.listen(local_engine, "rollback", on_rollback)
    event.listen(local_engine, "commit", on_commit)
    artifact_path = tmp_path / "a.json"
    try:
        with pytest.raises(RuntimeError):
            script_mod.run_dry_run(
                local_engine,
                artifact_path=artifact_path,
                company_ids=[],
                sample_size=None,
                reference_date=None,
                force=False,
            )
    finally:
        event.remove(local_engine, "rollback", on_rollback)
        event.remove(local_engine, "commit", on_commit)
    assert len(rollbacks) == 1
    assert len(commits) == 0
    assert not artifact_path.exists()


def _spy_session_class(script_mod, closed: list[int]):
    original_session_cls = script_mod.Session

    class SpySession(original_session_cls):
        def close(self):
            closed.append(1)
            super().close()

    return SpySession


def test_session_closed_explicitly_on_success(local_engine, tmp_path, monkeypatch):
    import scripts.run_track_record_shadow_dryrun as script_mod

    closed: list[int] = []
    monkeypatch.setattr(script_mod, "Session", _spy_session_class(script_mod, closed))

    script_mod.run_dry_run(
        local_engine,
        artifact_path=tmp_path / "a.json",
        company_ids=[],
        sample_size=None,
        reference_date=None,
        force=False,
    )
    assert closed == [1]


def test_session_closed_explicitly_on_exception(local_engine, tmp_path, monkeypatch):
    import scripts.run_track_record_shadow_dryrun as script_mod

    closed: list[int] = []
    monkeypatch.setattr(script_mod, "Session", _spy_session_class(script_mod, closed))

    def boom(session, **kwargs):
        raise RuntimeError("simulated backfill failure")

    monkeypatch.setattr(script_mod, "backfill_company_track_records", boom)

    with pytest.raises(RuntimeError):
        script_mod.run_dry_run(
            local_engine,
            artifact_path=tmp_path / "a.json",
            company_ids=[],
            sample_size=None,
            reference_date=None,
            force=False,
        )
    assert closed == [1]


def test_no_artifact_written_on_backfill_failure(local_engine, tmp_path, monkeypatch):
    import scripts.run_track_record_shadow_dryrun as script_mod

    def boom(session, **kwargs):
        raise RuntimeError("simulated backfill failure")

    monkeypatch.setattr(script_mod, "backfill_company_track_records", boom)

    artifact_path = tmp_path / "artifact.json"
    with pytest.raises(RuntimeError):
        script_mod.run_dry_run(
            local_engine,
            artifact_path=artifact_path,
            company_ids=[],
            sample_size=None,
            reference_date=None,
            force=False,
        )
    assert not artifact_path.exists()


def test_no_artifact_written_on_artifact_build_failure(
    local_engine, tmp_path, monkeypatch
):
    """A failure inside artifact aggregation itself (after a successful,
    real backfill call) must also leave no artifact on disk -- the write
    only happens after the whole try/finally block completes."""
    import scripts.run_track_record_shadow_dryrun as script_mod

    def boom(*args, **kwargs):
        raise RuntimeError("simulated aggregation failure")

    monkeypatch.setattr(script_mod, "build_shadow_dryrun_artifact", boom)

    artifact_path = tmp_path / "artifact.json"
    with pytest.raises(RuntimeError):
        script_mod.run_dry_run(
            local_engine,
            artifact_path=artifact_path,
            company_ids=[],
            sample_size=None,
            reference_date=None,
            force=False,
        )
    assert not artifact_path.exists()


def test_backfill_called_only_with_dry_run_true(local_engine, tmp_path, monkeypatch):
    import scripts.run_track_record_shadow_dryrun as script_mod

    captured_kwargs: dict = {}

    def spy(session, **kwargs):
        captured_kwargs.update(kwargs)
        return {
            "selected": 0,
            "processed": 0,
            "persisted": 0,
            "skipped": 0,
            "failed": 0,
            "dry_run": kwargs.get("dry_run"),
            "algorithm_version": "company_track_record_v1",
            "reference_date": "2026-07-20",
            "computed_at": "2026-07-20T00:00:00+00:00",
            "diagnostics_notes_count": 0,
            "errors": [],
            "results": [],
        }

    monkeypatch.setattr(script_mod, "backfill_company_track_records", spy)

    script_mod.run_dry_run(
        local_engine,
        artifact_path=tmp_path / "artifact.json",
        company_ids=[],
        sample_size=None,
        reference_date=None,
        force=False,
    )
    assert captured_kwargs.get("dry_run") is True


def _existing_company_ids(engine, limit: int = 2) -> list[int]:
    with engine.connect() as conn:
        rows = (
            conn.execute(
                text("SELECT id FROM companies ORDER BY id LIMIT :limit"),
                {"limit": limit},
            )
            .scalars()
            .all()
        )
    return list(rows)


def test_digest_reflects_real_selection_and_changes_with_scope(local_engine, tmp_path):
    """End-to-end (read-only) confirmation that the digest genuinely
    reflects the selected set for a real, non-empty selection, not just
    the empty-selection case exercised elsewhere. force=True is used so
    this doesn't depend on these companies' current track_record_version
    (unrelated to this test) -- and dry_run=True keeps it fully
    read-only regardless."""
    ids = _existing_company_ids(local_engine, limit=2)
    if len(ids) < 2:
        pytest.skip(
            "local companies table has fewer than 2 rows -- cannot exercise "
            "a real multi-row selection"
        )

    import scripts.run_track_record_shadow_dryrun as script_mod

    artifact_one = script_mod.run_dry_run(
        local_engine,
        artifact_path=tmp_path / "one.json",
        company_ids=[ids[0]],
        sample_size=None,
        reference_date=None,
        force=True,
    )
    artifact_two = script_mod.run_dry_run(
        local_engine,
        artifact_path=tmp_path / "two.json",
        company_ids=ids,
        sample_size=None,
        reference_date=None,
        force=True,
    )
    assert artifact_one["selected"] == 1
    assert artifact_two["selected"] == len(ids)
    assert artifact_one["eligibility_digest"] != artifact_two["eligibility_digest"]
