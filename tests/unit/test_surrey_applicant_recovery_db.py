"""Local-Postgres proof for the Surrey blank-only applicant writer."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from db.models import Permit
from pipeline.surrey_applicant_recovery import (
    apply_surrey_applicant_recovery,
    compute_recovery_digest,
)
from tests.db_test_safety import require_local_test_database


@pytest.fixture()
def db_session():
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
    session = Session(bind=conn)
    try:
        yield session
    finally:
        session.close()
        if outer.is_active:
            outer.rollback()
        conn.close()
        engine.dispose()


def test_real_update_changes_only_blank_applicant_and_never_commits(db_session):
    unique = uuid.uuid4().int % 1_000_000
    legacy_id = f"26-{unique:06d}-001-00"
    source_id = f"{legacy_id}/AB"
    applicant = "EN1D2 Database Proof Ltd."
    permit = Permit(
        address="123 Immutable Test Avenue",
        permit_type="Commercial Alteration",
        project_value="123456",
        applicant="",
        architect="Original Architect",
        issue_date="2026-07-20",
        description="Original Description",
        contractor="Original Contractor",
        source="surrey",
        city="Surrey",
        external_id=legacy_id,
        canonical_merge_confidence=0.75,
        canonical_merge_method="existing_method",
    )
    db_session.add(permit)
    db_session.flush()
    permit_id = int(permit.id)
    commits = []
    event.listen(db_session, "after_commit", lambda _session: commits.append(True))

    result = apply_surrey_applicant_recovery(
        db_session,
        source_rows=[
            {
                "PermitNumber": source_id,
                "ApplicantOrganization": applicant,
            }
        ],
        candidate_limit=1,
        expected_candidate_set_digest=compute_recovery_digest(
            [(permit_id, source_id, applicant)]
        ),
    )

    row = (
        db_session.connection()
        .execute(
            text(
                "SELECT applicant, address, permit_type, project_value, architect, "
                "issue_date, description, contractor, source, city, external_id, "
                "company_id, canonical_merge_confidence, canonical_merge_method "
                "FROM permits WHERE id = :permit_id"
            ),
            {"permit_id": permit_id},
        )
        .one()
    )
    assert result["updated_count"] == 1
    assert row.applicant == applicant
    assert row.address == "123 Immutable Test Avenue"
    assert row.permit_type == "Commercial Alteration"
    assert row.project_value == "123456"
    assert row.architect == "Original Architect"
    assert row.issue_date == "2026-07-20"
    assert row.description == "Original Description"
    assert row.contractor == "Original Contractor"
    assert row.source == "surrey"
    assert row.city == "Surrey"
    assert row.external_id == legacy_id
    assert row.company_id is None
    assert row.canonical_merge_confidence == 0.75
    assert row.canonical_merge_method == "existing_method"
    assert commits == []
