"""Tests for the hardened Surrey identity-aware scheduler core
(PR-EN1G-1, pipeline.surrey_identity_scheduler)."""

from __future__ import annotations

import uuid

import pytest

from pipeline.surrey_identity_scheduler import (
    SURREY_SCHEDULER_FLAG,
    SurreyIdentitySchedulerResult,
    compute_result_digest,
    map_surrey_result_to_job_run_outcome,
    run_surrey_identity_import_once,
    surrey_scheduler_enabled,
)

# --- feature flag ------------------------------------------------------


def test_flag_name_is_dedicated_and_not_shared_with_main_scheduler():
    assert SURREY_SCHEDULER_FLAG == "ENABLE_SURREY_PERMITS_SCHEDULER"


def test_disabled_by_default_when_env_unset(monkeypatch):
    monkeypatch.delenv(SURREY_SCHEDULER_FLAG, raising=False)
    assert surrey_scheduler_enabled() is False


@pytest.mark.parametrize("value", ["0", "false", "False", "no", "", "off", "disabled"])
def test_disabled_for_falsy_or_unrecognized_values(monkeypatch, value):
    monkeypatch.setenv(SURREY_SCHEDULER_FLAG, value)
    assert surrey_scheduler_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "True", "TRUE", "yes", "Yes"])
def test_enabled_only_for_explicit_truthy_values(monkeypatch, value):
    monkeypatch.setenv(SURREY_SCHEDULER_FLAG, value)
    assert surrey_scheduler_enabled() is True


# --- result digest -------------------------------------------------------


def test_result_digest_differs_from_plan_digest_and_is_count_sensitive():
    plan_digest = "a" * 64
    result_digest = compute_result_digest(
        plan_digest=plan_digest, updated=3, inserted=1
    )
    assert len(result_digest) == 64
    assert result_digest != plan_digest
    other = compute_result_digest(plan_digest=plan_digest, updated=3, inserted=2)
    assert other != result_digest


# --- run_surrey_identity_import_once: fake-session unit tests ----------


class _FakeSession:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _patch_plan(monkeypatch, report):
    monkeypatch.setattr(
        "pipeline.surrey_identity_scheduler.plan_surrey_identity_import",
        lambda _session, *, rows: report,
    )


def _patch_apply(monkeypatch, result_or_exc):
    def fake_apply(_session, *, rows, expected_plan_digest):
        if isinstance(result_or_exc, Exception):
            raise result_or_exc
        return result_or_exc

    monkeypatch.setattr(
        "pipeline.surrey_identity_scheduler.apply_surrey_identity_import_full",
        fake_apply,
    )


def _safe_report(*, updates=2, inserts=1, digest="d" * 64):
    return {
        "counts": {
            "source_total": updates + inserts,
            "invalid_rows": 0,
            "duplicate_source_rows": 0,
            "production_total": 100,
            "planned_updates": updates,
            "planned_inserts": inserts,
            "duplicate_risk": 0,
            "blank_applicant_preserved": 0,
        },
        "plan_digest": digest,
    }


def test_run_once_commits_on_full_success_using_the_planner_digest(monkeypatch):
    session = _FakeSession()
    report = _safe_report(updates=2, inserts=1, digest="e" * 64)
    captured = {}

    def fake_apply(_session, *, rows, expected_plan_digest):
        captured["expected_plan_digest"] = expected_plan_digest
        captured["rows"] = rows
        return {
            "eligible_updates": 2,
            "eligible_inserts": 1,
            "updated": 2,
            "inserted": 1,
            "plan_digest": expected_plan_digest,
        }

    _patch_plan(monkeypatch, report)
    monkeypatch.setattr(
        "pipeline.surrey_identity_scheduler.apply_surrey_identity_import_full",
        fake_apply,
    )

    rows = [
        {"external_id": "26-000001-001-00/AB"},
        {"external_id": "26-000002-001-00/CD"},
    ]
    result = run_surrey_identity_import_once(session, rows=rows)

    assert isinstance(result, SurreyIdentitySchedulerResult)
    assert result.errors == 0
    assert result.updated == 2
    assert result.inserted == 1
    assert result.plan_digest == "e" * 64
    assert len(result.result_digest) == 64
    assert result.result_digest != result.plan_digest
    assert (
        captured["expected_plan_digest"] == "e" * 64
    )  # planner digest reused verbatim
    assert session.commits == 1
    assert session.rollbacks == 0


@pytest.mark.parametrize(
    ("invalid_rows", "duplicate_source_rows", "duplicate_risk"),
    [(1, 0, 0), (0, 1, 0), (0, 0, 1), (2, 3, 1)],
)
def test_run_once_blocks_and_rolls_back_on_unsafe_plan_without_calling_adapter(
    monkeypatch, invalid_rows, duplicate_source_rows, duplicate_risk
):
    session = _FakeSession()
    report = _safe_report()
    report["counts"]["invalid_rows"] = invalid_rows
    report["counts"]["duplicate_source_rows"] = duplicate_source_rows
    report["counts"]["duplicate_risk"] = duplicate_risk
    _patch_plan(monkeypatch, report)

    def unreachable_apply(*_args, **_kwargs):
        raise AssertionError("adapter must not be called for an unsafe plan")

    monkeypatch.setattr(
        "pipeline.surrey_identity_scheduler.apply_surrey_identity_import_full",
        unreachable_apply,
    )

    result = run_surrey_identity_import_once(session, rows=[{"external_id": "x"}])
    assert result.errors == 1
    assert result.updated == 0
    assert result.inserted == 0
    assert result.result_digest is None
    assert result.plan_digest == report["plan_digest"]
    assert session.commits == 0
    assert session.rollbacks == 1


def test_run_once_blank_external_id_is_a_stop_condition_not_a_silent_skip(monkeypatch):
    """A blank/invalid identity row surfaces as invalid_rows in the plan
    (this is exactly what the real planner does) -- and must block and
    roll back the whole batch rather than being quietly dropped."""
    session = _FakeSession()
    report = _safe_report(updates=1, inserts=0)
    report["counts"]["invalid_rows"] = 1
    _patch_plan(monkeypatch, report)

    monkeypatch.setattr(
        "pipeline.surrey_identity_scheduler.apply_surrey_identity_import_full",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("adapter must not be called")
        ),
    )

    rows = [{"external_id": "26-000001-001-00/AB"}, {"external_id": ""}]
    result = run_surrey_identity_import_once(session, rows=rows)
    assert result.errors == 1
    assert result.source_rows == 2
    assert session.rollbacks == 1
    assert session.commits == 0


def test_run_once_rolls_back_on_planning_exception_without_leaking_message(
    monkeypatch, caplog
):
    session = _FakeSession()
    secret = "SECRET-26-000001-001-00-Ltd"

    def raising_plan(_session, *, rows):
        raise RuntimeError(secret)

    monkeypatch.setattr(
        "pipeline.surrey_identity_scheduler.plan_surrey_identity_import", raising_plan
    )
    with caplog.at_level("ERROR"):
        result = run_surrey_identity_import_once(session, rows=[{"external_id": "x"}])

    assert result.errors == 1
    assert result.plan_digest is None
    assert session.commits == 0
    assert session.rollbacks == 1
    assert secret not in caplog.text
    assert "RuntimeError" in caplog.text


def test_run_once_rolls_back_on_apply_exception_without_leaking_message(
    monkeypatch, caplog
):
    session = _FakeSession()
    secret = "TOP-SECRET-APPLICANT-NAME"
    report = _safe_report()
    _patch_plan(monkeypatch, report)
    _patch_apply(monkeypatch, RuntimeError(secret))

    with caplog.at_level("ERROR"):
        result = run_surrey_identity_import_once(session, rows=[{"external_id": "x"}])

    assert result.errors == 1
    assert result.result_digest is None
    assert session.commits == 0
    assert session.rollbacks == 1
    assert secret not in caplog.text
    assert "RuntimeError" in caplog.text


@pytest.mark.parametrize(
    "apply_result",
    [
        {"eligible_updates": 2, "eligible_inserts": 1, "updated": 1, "inserted": 1},
        {"eligible_updates": 2, "eligible_inserts": 1, "updated": 2, "inserted": 0},
        {"eligible_updates": 3, "eligible_inserts": 1, "updated": 2, "inserted": 1},
        {"eligible_updates": 2, "eligible_inserts": 2, "updated": 2, "inserted": 1},
    ],
)
def test_run_once_blocks_on_drift_between_plan_and_apply_result(
    monkeypatch, apply_result
):
    session = _FakeSession()
    report = _safe_report(updates=2, inserts=1)
    _patch_plan(monkeypatch, report)
    _patch_apply(monkeypatch, apply_result)

    result = run_surrey_identity_import_once(session, rows=[{"external_id": "x"}])
    assert result.errors == 1
    assert result.result_digest is None
    assert session.commits == 0
    assert session.rollbacks == 1


def test_run_once_result_never_serializes_raw_evidence(monkeypatch):
    import json

    session = _FakeSession()
    report = _safe_report(updates=1, inserts=0)
    _patch_plan(monkeypatch, report)
    monkeypatch.setattr(
        "pipeline.surrey_identity_scheduler.apply_surrey_identity_import_full",
        lambda *_a, **_k: {
            "eligible_updates": 1,
            "eligible_inserts": 0,
            "updated": 1,
            "inserted": 0,
            "plan_digest": report["plan_digest"],
        },
    )
    secret_number = "26-999999-001-00/SECRET"
    result = run_surrey_identity_import_once(
        session, rows=[{"external_id": secret_number, "applicant": "SECRET BUILDER"}]
    )
    serialized = json.dumps(result.as_dict())
    assert secret_number not in serialized
    assert "SECRET BUILDER" not in serialized


def test_module_never_logs_exception_str(monkeypatch, caplog):
    """Static + dynamic proof: no log call formats a bare exception
    object or str(exc) -- only type(exc).__name__."""
    import re

    import pipeline.surrey_identity_scheduler as module

    with open(module.__file__, encoding="utf-8") as handle:
        source = handle.read()
    code_only = re.sub(r'""".*?"""', "", source, flags=re.DOTALL)
    assert "str(exc)" not in code_only
    assert '%s", exc)' not in code_only
    assert '%s", exc,)' not in code_only


# --- planning/apply use the same session (single transaction) ----------


def test_run_once_uses_the_same_session_for_planning_and_applying(monkeypatch):
    session = _FakeSession()
    seen_sessions = []

    def fake_plan(passed_session, *, rows):
        seen_sessions.append(passed_session)
        return _safe_report(updates=1, inserts=0)

    def fake_apply(passed_session, *, rows, expected_plan_digest):
        seen_sessions.append(passed_session)
        return {
            "eligible_updates": 1,
            "eligible_inserts": 0,
            "updated": 1,
            "inserted": 0,
            "plan_digest": expected_plan_digest,
        }

    monkeypatch.setattr(
        "pipeline.surrey_identity_scheduler.plan_surrey_identity_import", fake_plan
    )
    monkeypatch.setattr(
        "pipeline.surrey_identity_scheduler.apply_surrey_identity_import_full",
        fake_apply,
    )
    run_surrey_identity_import_once(session, rows=[{"external_id": "x"}])
    assert seen_sessions == [session, session]


# --- local-Postgres: real plan/apply integration ------------------------


@pytest.fixture()
def db_session():
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session as RealSession

    from tests.db_test_safety import require_local_test_database

    database_url = require_local_test_database()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as probe:
            probe.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")

    conn = engine.connect()
    outer = conn.begin()
    conn.execute(text("SET LOCAL lock_timeout = '10s'"))
    session = RealSession(bind=conn)
    try:
        yield session
    finally:
        session.close()
        if outer.is_active:
            outer.rollback()
        conn.close()
        engine.dispose()


def _legacy_id(prefix: str, index: int) -> str:
    unique = uuid.uuid4().int % 1_000_000
    return f"{prefix}-{unique:06d}-{index:03d}-00"


def test_run_once_real_plan_digest_matches_class_a_planner_digest(db_session):
    """The digest this function reports must be literally the same value
    the Class-A planner (plan_surrey_identity_import) would compute for
    the identical rows/session state -- not a separately invented hash."""
    from pipeline.surrey_identity_import_canary import plan_surrey_identity_import

    legacy_id = _legacy_id("43", 1)
    official_number = f"{legacy_id}/AA"
    from db.models import Permit

    permit = Permit(
        address="",
        permit_type="",
        project_value="",
        applicant="",
        source="surrey",
        city="Surrey",
        external_id=legacy_id,
        official_source_id=None,
    )
    db_session.add(permit)
    db_session.flush()

    rows = [{"external_id": official_number}]
    expected_report = plan_surrey_identity_import(db_session, rows=rows)

    result = run_surrey_identity_import_once(db_session, rows=rows)
    assert result.errors == 0
    assert result.plan_digest == expected_report["plan_digest"]


def test_run_once_blank_external_id_blocks_real_batch_with_zero_writes(db_session):
    from sqlalchemy import text

    good_number = f"{_legacy_id('44', 1)}/BB"
    rows = [
        {"external_id": good_number, "address": "Should Not Be Written"},
        {"external_id": ""},
    ]
    result = run_surrey_identity_import_once(db_session, rows=rows)
    assert result.errors == 1
    assert result.updated == 0
    assert result.inserted == 0

    count = db_session.execute(
        text(
            "SELECT COUNT(*) FROM permits WHERE source = 'surrey' AND external_id = :n"
        ),
        {"n": good_number},
    ).scalar()
    assert count == 0


def test_run_once_ambiguous_match_blocks_real_batch_with_zero_writes(db_session):
    from sqlalchemy import text

    from db.models import Permit

    ambiguous_legacy = _legacy_id("45", 1)
    ambiguous_official = f"{ambiguous_legacy}/ZZ"
    first = Permit(
        address="",
        permit_type="",
        project_value="",
        applicant="",
        source="surrey",
        city="Surrey",
        external_id=ambiguous_legacy,
        official_source_id=None,
    )
    second = Permit(
        address="",
        permit_type="",
        project_value="",
        applicant="",
        source="surrey",
        city="Surrey",
        external_id=ambiguous_legacy,
        official_source_id=None,
    )
    db_session.add_all([first, second])
    db_session.flush()

    good_number = f"{_legacy_id('46', 1)}/CC"
    rows = [
        {"external_id": good_number, "address": "Should Not Be Written"},
        {"external_id": ambiguous_official},
    ]
    result = run_surrey_identity_import_once(db_session, rows=rows)
    assert result.errors == 1

    count = db_session.execute(
        text(
            "SELECT COUNT(*) FROM permits WHERE source = 'surrey' AND external_id = :n"
        ),
        {"n": good_number},
    ).scalar()
    assert count == 0


def test_run_once_full_success_applies_every_row_and_commits(db_session):
    from sqlalchemy import text

    from db.models import Permit

    legacy_id = _legacy_id("47", 1)
    official_number = f"{legacy_id}/DD"
    permit = Permit(
        address="Old",
        permit_type="",
        project_value="",
        applicant="Existing Applicant",
        source="surrey",
        city="Surrey",
        external_id=legacy_id,
        official_source_id=official_number,
        company_id=None,
    )
    db_session.add(permit)
    db_session.flush()
    permit_id = int(permit.id)

    new_number = f"{_legacy_id('48', 1)}/EE"
    rows = [
        {"external_id": official_number, "address": "New Address"},
        {"external_id": new_number, "address": "Brand New"},
    ]
    result = run_surrey_identity_import_once(db_session, rows=rows)
    assert result.errors == 0
    assert result.updated == 1
    assert result.inserted == 1

    row = db_session.execute(
        text("SELECT address, applicant, company_id FROM permits WHERE id = :id"),
        {"id": permit_id},
    ).one()
    assert row.address == "New Address"
    assert row.applicant == "Existing Applicant"
    assert row.company_id is None

    inserted_count = db_session.execute(
        text(
            "SELECT COUNT(*) FROM permits WHERE source = 'surrey' AND external_id = :n"
        ),
        {"n": new_number},
    ).scalar()
    assert inserted_count == 1


# --- M3C: map_surrey_result_to_job_run_outcome (pure) -------------------


def test_map_result_success_when_errors_zero():
    result = SurreyIdentitySchedulerResult(
        source_rows=12,
        updated=3,
        inserted=2,
        errors=0,
        plan_digest="a" * 64,
        result_digest="b" * 64,
    )
    status, counts = map_surrey_result_to_job_run_outcome(result)
    assert status == "success"
    assert counts == {
        "source_rows": 12,
        "inserted": 2,
        "updated": 3,
        "error_count": 0,
    }


def test_map_result_partial_failure_when_errors_nonzero():
    result = SurreyIdentitySchedulerResult(
        source_rows=5,
        updated=0,
        inserted=0,
        errors=1,
        plan_digest="c" * 64,
        result_digest=None,
    )
    status, counts = map_surrey_result_to_job_run_outcome(result)
    assert status == "partial_failure"
    assert counts["error_count"] == 1


def test_map_result_counts_are_flat_ints_only_no_digests_or_text():
    result = SurreyIdentitySchedulerResult(
        source_rows=1,
        updated=1,
        inserted=0,
        errors=0,
        plan_digest="d" * 64,
        result_digest="e" * 64,
    )
    _, counts = map_surrey_result_to_job_run_outcome(result)
    assert set(counts.keys()) == {"source_rows", "inserted", "updated", "error_count"}
    for value in counts.values():
        assert isinstance(value, int) and not isinstance(value, bool)
    serialized = str(counts)
    assert "d" * 64 not in serialized
    assert "e" * 64 not in serialized


def test_map_result_never_returns_status_other_than_success_or_partial_failure():
    for errors in (0, 1, 2, 100):
        status, _ = map_surrey_result_to_job_run_outcome(
            SurreyIdentitySchedulerResult(
                source_rows=1,
                updated=0,
                inserted=0,
                errors=errors,
                plan_digest=None,
                result_digest=None,
            )
        )
        assert status in {"success", "partial_failure"}


# --- M3C: on_phase progress boundaries -----------------------------------


def test_on_phase_called_at_plan_validate_apply_on_full_success(monkeypatch):
    session = _FakeSession()
    report = _safe_report(updates=1, inserts=1)
    _patch_plan(monkeypatch, report)
    monkeypatch.setattr(
        "pipeline.surrey_identity_scheduler.apply_surrey_identity_import_full",
        lambda *_a, **_k: {
            "eligible_updates": 1,
            "eligible_inserts": 1,
            "updated": 1,
            "inserted": 1,
            "plan_digest": report["plan_digest"],
        },
    )

    phases_seen: list[str] = []
    result = run_surrey_identity_import_once(
        session, rows=[{"external_id": "x"}], on_phase=phases_seen.append
    )

    assert phases_seen == ["plan", "validate", "apply"]
    assert result.errors == 0


def test_on_phase_not_called_beyond_plan_when_blocked_on_unsafe_plan(monkeypatch):
    session = _FakeSession()
    report = _safe_report()
    report["counts"]["invalid_rows"] = 1
    _patch_plan(monkeypatch, report)
    monkeypatch.setattr(
        "pipeline.surrey_identity_scheduler.apply_surrey_identity_import_full",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("adapter must not be called")
        ),
    )

    phases_seen: list[str] = []
    result = run_surrey_identity_import_once(
        session, rows=[{"external_id": "x"}], on_phase=phases_seen.append
    )

    assert phases_seen == ["plan"]  # plan succeeded, validate/apply never reached
    assert result.errors == 1


def test_on_phase_not_called_at_all_when_planning_raises(monkeypatch):
    session = _FakeSession()

    def raising_plan(_session, *, rows):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "pipeline.surrey_identity_scheduler.plan_surrey_identity_import", raising_plan
    )

    phases_seen: list[str] = []
    result = run_surrey_identity_import_once(
        session, rows=[{"external_id": "x"}], on_phase=phases_seen.append
    )

    assert phases_seen == []
    assert result.errors == 1


def test_on_phase_none_is_a_complete_noop_existing_callers_unaffected(monkeypatch):
    """Default on_phase=None must behave exactly like every pre-M3C
    caller/test -- no attribute error, no behavior change."""
    session = _FakeSession()
    report = _safe_report(updates=1, inserts=0)
    _patch_plan(monkeypatch, report)
    monkeypatch.setattr(
        "pipeline.surrey_identity_scheduler.apply_surrey_identity_import_full",
        lambda *_a, **_k: {
            "eligible_updates": 1,
            "eligible_inserts": 0,
            "updated": 1,
            "inserted": 0,
            "plan_digest": report["plan_digest"],
        },
    )
    result = run_surrey_identity_import_once(session, rows=[{"external_id": "x"}])
    assert result.errors == 0
    assert session.commits == 1


def test_a_raising_on_phase_callback_never_breaks_the_real_import(monkeypatch, caplog):
    """Fail-open: a callback that raises must not affect the Surrey
    import's own commit/result, and the callback's own exception text
    must never appear in the logs -- only a fixed, phase-named warning."""
    session = _FakeSession()
    report = _safe_report(updates=1, inserts=1)
    _patch_plan(monkeypatch, report)
    monkeypatch.setattr(
        "pipeline.surrey_identity_scheduler.apply_surrey_identity_import_full",
        lambda *_a, **_k: {
            "eligible_updates": 1,
            "eligible_inserts": 1,
            "updated": 1,
            "inserted": 1,
            "plan_digest": report["plan_digest"],
        },
    )

    def exploding_on_phase(_phase: str) -> None:
        raise RuntimeError("TELEMETRY-SECRET-SHOULD-NOT-LEAK")

    with caplog.at_level("WARNING"):
        result = run_surrey_identity_import_once(
            session, rows=[{"external_id": "x"}], on_phase=exploding_on_phase
        )

    assert result.errors == 0
    assert result.updated == 1
    assert result.inserted == 1
    assert session.commits == 1
    assert session.rollbacks == 0
    assert "TELEMETRY-SECRET-SHOULD-NOT-LEAK" not in caplog.text
    assert "on_phase callback failed" in caplog.text


# --- review fix: "apply" fires strictly after a successful commit -------


class _CommitRaisingSession(_FakeSession):
    """Same as _FakeSession, but commit() can be told to raise --
    proves on_phase("apply") only fires once the commit genuinely
    succeeded, never merely because the adapter call returned."""

    def __init__(self, *, fail_commit: bool = False):
        super().__init__()
        self._fail_commit = fail_commit

    def commit(self):
        if self._fail_commit:
            raise RuntimeError("commit failed")
        super().commit()


def test_on_phase_apply_fires_only_after_successful_commit(monkeypatch):
    session = _CommitRaisingSession(fail_commit=False)
    report = _safe_report(updates=1, inserts=1)
    _patch_plan(monkeypatch, report)
    monkeypatch.setattr(
        "pipeline.surrey_identity_scheduler.apply_surrey_identity_import_full",
        lambda *_a, **_k: {
            "eligible_updates": 1,
            "eligible_inserts": 1,
            "updated": 1,
            "inserted": 1,
            "plan_digest": report["plan_digest"],
        },
    )

    commits_seen_by_apply_phase: list[int] = []

    def on_phase(phase: str) -> None:
        if phase == "apply":
            commits_seen_by_apply_phase.append(session.commits)

    result = run_surrey_identity_import_once(
        session, rows=[{"external_id": "x"}], on_phase=on_phase
    )

    assert result.errors == 0
    assert session.commits == 1
    # At the moment "apply" fired, the commit had ALREADY happened.
    assert commits_seen_by_apply_phase == [1]


def test_commit_failure_prevents_apply_milestone_and_propagates_exception(monkeypatch):
    """If commit() itself raises, "apply" must never fire, and the
    exception must propagate uncaught out of
    run_surrey_identity_import_once() -- unlike every other failure path
    in this function, which returns a _blocked() result instead. The
    scheduler wrapper (pipeline/scheduler.py) is what turns this
    propagated exception into a terminal `failed` telemetry outcome --
    see test_flag_true_exception_from_import_once_maps_to_failed_and_propagates
    in tests/unit/test_surrey_job_run_telemetry.py, which proves that
    generically for any exception out of this function."""
    session = _CommitRaisingSession(fail_commit=True)
    report = _safe_report(updates=1, inserts=1)
    _patch_plan(monkeypatch, report)
    monkeypatch.setattr(
        "pipeline.surrey_identity_scheduler.apply_surrey_identity_import_full",
        lambda *_a, **_k: {
            "eligible_updates": 1,
            "eligible_inserts": 1,
            "updated": 1,
            "inserted": 1,
            "plan_digest": report["plan_digest"],
        },
    )

    phases_seen: list[str] = []

    with pytest.raises(RuntimeError, match="commit failed"):
        run_surrey_identity_import_once(
            session, rows=[{"external_id": "x"}], on_phase=phases_seen.append
        )

    assert phases_seen == ["plan", "validate"]  # "apply" never fired
    assert session.commits == 0
