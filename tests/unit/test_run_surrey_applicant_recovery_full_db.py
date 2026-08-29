"""Local-Postgres regression tests for the EN1D-4 Class-C full-recovery runner."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

import scripts.run_surrey_applicant_recovery_full as runner
from db.models import Permit
from pipeline.surrey_applicant_recovery import compute_recovery_digest
from tests.db_test_safety import require_local_test_database
from tests.db_transactional_fixture import transactional_session


@pytest.fixture()
def db_session():
    """runner.execute_full_recovery() itself calls session.rollback() on
    any failure (it owns the transaction by design). A plain outer
    transaction is not enough to protect this test's own setup rows from
    that rollback; see tests/db_transactional_fixture.py.
    """
    database_url = require_local_test_database()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as probe:
            probe.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")

    try:
        with transactional_session(engine) as session:
            yield session
    finally:
        engine.dispose()


def _make_permit(index: int) -> tuple[Permit, str, str]:
    unique = uuid.uuid4().int % 1_000_000
    legacy_id = f"28-{unique:06d}-{index:03d}-00"
    source_id = f"{legacy_id}/EF"
    applicant = f"EN1D4 Full Recovery Proof {index} Ltd."
    permit = Permit(
        address=f"{index} Full Recovery Avenue",
        permit_type="Commercial Alteration",
        project_value="1000",
        applicant="",
        source="surrey",
        city="Surrey",
        external_id=legacy_id,
    )
    return permit, source_id, applicant


def test_full_recovery_updates_exact_rowcount_and_only_applicant(db_session):
    companies_before = (
        db_session.connection().execute(text("SELECT COUNT(*) FROM companies")).scalar()
    )
    permits_and_evidence = [_make_permit(i) for i in range(3)]
    for permit, _source_id, _applicant in permits_and_evidence:
        db_session.add(permit)
    db_session.flush()

    entries = [
        (int(permit.id), source_id, applicant)
        for permit, source_id, applicant in permits_and_evidence
    ]
    source_rows = [
        {"PermitNumber": source_id, "ApplicantOrganization": applicant}
        for _permit, source_id, applicant in permits_and_evidence
    ]
    artifact = {
        "candidate_count": 3,
        "candidate_set_digest": compute_recovery_digest(entries),
    }

    result = runner.execute_full_recovery(
        db_session,
        source_rows=source_rows,
        artifact=artifact,
    )
    assert result["updated_count"] == 3
    assert result["selected_count"] == 3

    for permit_id, _source_id, expected_applicant in entries:
        row = (
            db_session.connection()
            .execute(
                text(
                    "SELECT applicant, address, permit_type, project_value, "
                    "source, city, external_id, company_id "
                    "FROM permits WHERE id = :permit_id"
                ),
                {"permit_id": permit_id},
            )
            .one()
        )
        assert row.applicant == expected_applicant
        assert row.company_id is None

    companies_after = (
        db_session.connection().execute(text("SELECT COUNT(*) FROM companies")).scalar()
    )
    assert companies_after == companies_before


def test_full_recovery_rejects_and_does_not_write_on_partial_source_mismatch(
    db_session,
):
    """If the artifact's candidate_count no longer matches the live re-plan
    (e.g. one row was externally recovered already), the whole transaction is
    rolled back before any row is touched -- not partially applied."""
    permits_and_evidence = [_make_permit(i) for i in range(2)]
    for permit, _source_id, _applicant in permits_and_evidence:
        db_session.add(permit)
    # commit (not flush): execute_full_recovery rolls back on failure, and
    # under join_transaction_mode="create_savepoint" a rollback only
    # undoes the current savepoint -- committing here releases it and
    # opens a fresh one, so this setup survives that internal rollback
    # while still living entirely inside the fixture's own outer
    # transaction (never touches real data; rolled back at teardown).
    db_session.commit()

    entries = [
        (int(permit.id), source_id, applicant)
        for permit, source_id, applicant in permits_and_evidence
    ]
    source_rows = [
        {"PermitNumber": source_id, "ApplicantOrganization": applicant}
        for _permit, source_id, applicant in permits_and_evidence
    ]
    # Artifact claims 3 candidates existed, but only 2 are actually eligible --
    # simulates a stale artifact reviewed against a since-changed data set.
    artifact = {
        "candidate_count": 3,
        "candidate_set_digest": "0" * 64,
    }

    with pytest.raises(Exception, match="candidate set changed"):
        runner.execute_full_recovery(
            db_session,
            source_rows=source_rows,
            artifact=artifact,
        )

    for permit, _source_id, _applicant in permits_and_evidence:
        row = (
            db_session.connection()
            .execute(
                text("SELECT applicant FROM permits WHERE id = :permit_id"),
                {"permit_id": int(permit.id)},
            )
            .one()
        )
        assert row.applicant == ""


def test_full_recovery_rolls_back_with_no_partial_rows_when_live_eligible_count_grew(
    db_session,
):
    """The writer's digest check only compares the reviewed candidate_count
    slice (ordered by Permit.id), so it can still match and actually execute
    those updates in-session even when the live eligible universe has grown
    beyond what was reviewed. The eligible_count check must catch that drift
    and roll back -- proving the already-executed, uncommitted writes are
    fully reverted, not partially applied."""
    permits_and_evidence = [_make_permit(i) for i in range(3)]
    for permit, _source_id, _applicant in permits_and_evidence:
        db_session.add(permit)
    db_session.commit()  # see comment in the previous test for why commit, not flush
    permits_and_evidence.sort(key=lambda pe: int(pe[0].id))

    entries_all = [
        (int(permit.id), source_id, applicant)
        for permit, source_id, applicant in permits_and_evidence
    ]
    source_rows = [
        {"PermitNumber": source_id, "ApplicantOrganization": applicant}
        for _permit, source_id, applicant in permits_and_evidence
    ]
    # Artifact reviewed only the first 2 (by id); the live plan now has 3
    # eligible candidates. The digest still matches for the reviewed slice.
    reviewed = entries_all[:2]
    artifact = {
        "candidate_count": 2,
        "candidate_set_digest": compute_recovery_digest(reviewed),
    }

    with pytest.raises(runner.SurreyApplicantFullRecoveryError, match="eligible"):
        runner.execute_full_recovery(
            db_session,
            source_rows=source_rows,
            artifact=artifact,
        )

    for permit_id, _source_id, _applicant in entries_all:
        row = (
            db_session.connection()
            .execute(
                text("SELECT applicant FROM permits WHERE id = :permit_id"),
                {"permit_id": permit_id},
            )
            .one()
        )
        assert row.applicant == ""


def test_full_recovery_is_idempotent_and_rejects_stale_artifact_after_success():
    """End-to-end proof against a real commit: a second run with the same
    (now-stale) artifact must fail closed instead of re-applying or corrupting
    the already-recovered value."""
    database_url = require_local_test_database()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as probe:
            probe.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")

    permit_id: int | None = None
    session = Session(bind=engine)
    try:
        permit, source_id, applicant = _make_permit(99)
        session.add(permit)
        session.commit()
        permit_id = int(permit.id)

        artifact = {
            "candidate_count": 1,
            "candidate_set_digest": compute_recovery_digest(
                [(permit_id, source_id, applicant)]
            ),
        }
        source_rows = [{"PermitNumber": source_id, "ApplicantOrganization": applicant}]

        result = runner.execute_full_recovery(
            session, source_rows=source_rows, artifact=artifact
        )
        assert result["updated_count"] == 1

        row = (
            session.connection()
            .execute(
                text("SELECT applicant FROM permits WHERE id = :id"), {"id": permit_id}
            )
            .one()
        )
        assert row.applicant == applicant

        # A fresh dry-run at this point would report candidate_count=0 for this
        # row; re-using the original (now stale) artifact must fail closed.
        with pytest.raises(Exception, match="candidate set changed"):
            runner.execute_full_recovery(
                session, source_rows=source_rows, artifact=artifact
            )

        row_after = (
            session.connection()
            .execute(
                text("SELECT applicant FROM permits WHERE id = :id"), {"id": permit_id}
            )
            .one()
        )
        assert row_after.applicant == applicant
    finally:
        session.close()
        if permit_id is not None:
            cleanup = create_engine(database_url)
            with cleanup.connect() as conn:
                conn.execute(
                    text("DELETE FROM permits WHERE id = :id"), {"id": permit_id}
                )
                conn.commit()
            cleanup.dispose()
        engine.dispose()
