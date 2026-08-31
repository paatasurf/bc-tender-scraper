"""Real local-Postgres tests for pipeline/company_enrichment/orchestrator.py's
provider cascade and writer: golden case #6 (provider timeout/error
isolation -- the cascade continues, the profile is never left in an error
state), golden case #9 (no match from any provider is still a valid,
successful run, never an error), and the verified-field protection
carve-out of golden case #8 (a verified=True field is never overwritten by
an automated result).

Uses small fake EnrichmentProvider implementations (not OrgBookAdapter) so
the cascade-continuation and timeout-isolation mechanics are tested
directly, independent of what OrgBook itself returns -- this phase wires
only OrgBookAdapter by default in production (_default_providers() in
orchestrator.py), but the cascade logic itself must work for any future provider
(website, Google, RFC Phases 3/6) without a rewrite.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from db.company_enrichment_ddl import company_enrichment_migration_statements
from db.company_enrichment_tables import company_enrichment_fields
from db.models import Company
from pipeline.company_enrichment.orchestrator import (
    _resolve_cascade_status,
    run_cascade_for_job,
    start_or_join_job,
)
from pipeline.company_enrichment.provider import EnrichmentRequest, ProviderFact, ProviderResult
from tests.db_test_safety import require_local_test_database


@pytest.mark.parametrize(
    "providers_attempted,expected",
    [
        ([], "success"),
        (["orgbook:ok"], "success"),
        (["orgbook:ok", "website:ok"], "success"),  # clean, even with zero matches -- golden case #9
        (["orgbook:error"], "failed"),
        (["orgbook:timeout"], "failed"),
        (["orgbook:error", "website:timeout"], "failed"),  # every attempt broke
        (["orgbook:ok", "website:error"], "partial_success"),
        (["orgbook:timeout", "website:ok"], "partial_success"),
        (["orgbook:ok", "website:error", "google:timeout"], "partial_success"),
    ],
)
def test_resolve_cascade_status_truth_table(providers_attempted, expected) -> None:
    """Pure-function lock-in of the semantic-status review's exact
    classification rule -- no DB, no provider calls, just the
    ok/error/timeout tag-counting logic itself."""
    assert _resolve_cascade_status(providers_attempted) == expected


class FakeProvider:
    def __init__(self, name: str, result: ProviderResult | None = None, raises: Exception | None = None, sleep_s: float = 0.0):
        self.name = name
        self.is_fact_source = True
        self._result = result
        self._raises = raises
        self._sleep_s = sleep_s

    def lookup(self, session, request: EnrichmentRequest) -> ProviderResult:
        if self._sleep_s:
            time.sleep(self._sleep_s)
        if self._raises is not None:
            raise self._raises
        return self._result


@pytest.fixture
def enrichment_db():
    database_url = require_local_test_database()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as probe:
            probe.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")

    with engine.begin() as conn:
        for statement in company_enrichment_migration_statements():
            conn.execute(text(statement))

    with Session(engine) as session:
        company = Company(name="Orchestrator Test Co Ltd")
        session.add(company)
        session.commit()
        company_id = company.id

    def _reset() -> None:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM company_enrichment_fields WHERE company_id = :id"), {"id": company_id})
            conn.execute(text("DELETE FROM company_enrichment_jobs WHERE company_id = :id"), {"id": company_id})
            conn.execute(text("DELETE FROM companies WHERE id = :id"), {"id": company_id})

    try:
        yield engine, company_id
    finally:
        _reset()
        engine.dispose()


def _job_status(engine, run_id: str) -> str:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT status FROM company_enrichment_jobs WHERE run_id = :run_id"),
            {"run_id": run_id},
        ).scalar_one()


def test_no_match_from_any_provider_is_a_valid_success_not_an_error(enrichment_db) -> None:
    engine, company_id = enrichment_db
    with Session(engine) as session:
        run_id, _ = start_or_join_job(session, company_id, trigger="profile_view")

    provider = FakeProvider("stub", result=ProviderResult(provider="stub", matched=False))
    with Session(engine) as session:
        result = run_cascade_for_job(
            session, run_id, company_id, "Orchestrator Test Co Ltd", providers=(provider,)
        )

    assert result["status"] == "success"
    assert result["matched"] is False
    assert result["fields_written"] == ()
    assert _job_status(engine, run_id) == "success"


def test_a_raising_provider_is_isolated_and_the_cascade_continues_as_partial_success(
    enrichment_db,
) -> None:
    """Semantic-status review finding: one provider erroring while another
    succeeds must be reported as "partial_success" (mirrors
    pipeline/runs.py::_resolve_status()'s write_failures-with-progress
    rule), never a plain "success" that hides the fact something broke."""
    engine, company_id = enrichment_db
    with Session(engine) as session:
        run_id, _ = start_or_join_job(session, company_id, trigger="profile_view")

    broken = FakeProvider("broken", raises=RuntimeError("boom"))
    healthy = FakeProvider(
        "healthy",
        result=ProviderResult(
            provider="healthy",
            matched=True,
            facts=(ProviderFact(field_name="legal_name", value="Orchestrator Test Co Ltd.", confidence=0.8),),
        ),
    )
    with Session(engine) as session:
        result = run_cascade_for_job(
            session, run_id, company_id, "Orchestrator Test Co Ltd", providers=(broken, healthy)
        )

    assert result["status"] == "partial_success"
    assert "broken:error" in result["providers_attempted"]
    assert "healthy:ok" in result["providers_attempted"]
    assert result["fields_written"] == ("legal_name",)
    assert _job_status(engine, run_id) == "partial_success"


def test_two_successful_providers_each_write_their_own_source_not_misattributed(
    enrichment_db,
) -> None:
    """Pre-PR review finding: with only ONE successful provider ever
    exercised in the earlier test suite, a real attribution bug (all
    facts written under providers[0].name regardless of which provider
    actually produced them) was never caught by any test. This is the
    regression guard for that fix -- two DIFFERENT providers, each
    contributing a DIFFERENT field, must each be persisted under their
    OWN source, not the first provider's name."""
    engine, company_id = enrichment_db
    with Session(engine) as session:
        run_id, _ = start_or_join_job(session, company_id, trigger="profile_view")

    first = FakeProvider(
        "orgbook",
        result=ProviderResult(
            provider="orgbook",
            matched=True,
            facts=(ProviderFact(field_name="legal_name", value="Orchestrator Test Co Ltd.", confidence=0.9),),
        ),
    )
    second = FakeProvider(
        "website",
        result=ProviderResult(
            provider="website",
            matched=True,
            facts=(ProviderFact(field_name="phone", value="+1 604 555 0100", confidence=0.7),),
        ),
    )
    with Session(engine) as session:
        result = run_cascade_for_job(
            session, run_id, company_id, "Orchestrator Test Co Ltd", providers=(first, second)
        )

    assert set(result["fields_written"]) == {"legal_name", "phone"}

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT field_name, source FROM company_enrichment_fields "
                "WHERE company_id = :id AND superseded_at IS NULL"
            ),
            {"id": company_id},
        ).mappings().all()
    source_by_field = {r["field_name"]: r["source"] for r in rows}
    assert source_by_field == {"legal_name": "orgbook", "phone": "website"}


def test_two_providers_disagreeing_on_the_same_field_both_coexist_not_overwritten(
    enrichment_db,
) -> None:
    """RFC S6 cross-provider merge rule: when two providers disagree on
    the SAME field, both are kept (superseded only within the SAME
    source) -- never a silent overwrite of one provider's claim by
    another's. This is distinct from the verified-field protection test
    (that guards a verified=True row against ANY automated source; this
    guards two automated, non-verified sources against each other)."""
    engine, company_id = enrichment_db
    with Session(engine) as session:
        run_id, _ = start_or_join_job(session, company_id, trigger="profile_view")

    orgbook = FakeProvider(
        "orgbook",
        result=ProviderResult(
            provider="orgbook",
            matched=True,
            facts=(ProviderFact(field_name="city", value="Vancouver", confidence=0.9),),
        ),
    )
    website = FakeProvider(
        "website",
        result=ProviderResult(
            provider="website",
            matched=True,
            facts=(ProviderFact(field_name="city", value="Burnaby", confidence=0.6),),
        ),
    )
    with Session(engine) as session:
        run_cascade_for_job(session, run_id, company_id, "Orchestrator Test Co Ltd", providers=(orgbook, website))

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT source, value FROM company_enrichment_fields "
                "WHERE company_id = :id AND field_name = 'city' AND superseded_at IS NULL"
            ),
            {"id": company_id},
        ).mappings().all()
    by_source = {r["source"]: r["value"] for r in rows}
    assert by_source == {"orgbook": "Vancouver", "website": "Burnaby"}


def test_a_slow_provider_past_the_timeout_is_marked_partial_success_not_hidden_as_success(
    enrichment_db,
) -> None:
    """Golden case #6 (a provider that takes too long does not fail the
    whole job, and the cascade proceeds to the next provider) COMBINED
    with the semantic-status review finding: a timeout is a real,
    partial failure -- it must be visible in the job's own terminal
    status as "partial_success", not silently collapsed into "success"
    the way it was before this review (every provider outcome, including
    timeout and error, used to map to the same "success" regardless).
    Already-committed data (the fast provider's facts) is unaffected
    either way."""
    engine, company_id = enrichment_db
    with Session(engine) as session:
        run_id, _ = start_or_join_job(session, company_id, trigger="profile_view")

    slow = FakeProvider(
        "slow",
        result=ProviderResult(provider="slow", matched=True, facts=(ProviderFact("legal_name", "Wrong Co", 0.5),)),
        sleep_s=0.15,
    )
    fast = FakeProvider(
        "fast",
        result=ProviderResult(provider="fast", matched=True, facts=(ProviderFact("business_number", "BC7654321", 0.9),)),
    )
    with Session(engine) as session:
        result = run_cascade_for_job(
            session,
            run_id,
            company_id,
            "Orchestrator Test Co Ltd",
            providers=(slow, fast),
            timeout_s=0.05,
        )

    assert result["status"] == "partial_success"
    assert "slow:timeout" in result["providers_attempted"]
    assert "fast:ok" in result["providers_attempted"]
    # the timed-out provider's facts must never be written
    assert "legal_name" not in result["fields_written"]
    assert "business_number" in result["fields_written"]

    # Direct DB check (not just the returned dict): a provider timeout
    # must never leave the JOB ROW itself stuck 'running' forever -- it
    # must reach a genuine terminal status, and that status must honestly
    # reflect the partial failure, not read as a clean success.
    assert _job_status(engine, run_id) == "partial_success"


def test_every_provider_erroring_is_reported_failed_not_partial_success(enrichment_db) -> None:
    """The third branch of the semantic-status split: if EVERY attempted
    provider errors/times out (zero clean successes), the job is
    "failed" -- categorically different from a clean no-match (golden
    case #9, still "success") and from a mixed outcome ("partial_success").
    Nothing was verified to be absent here; the cascade itself could not
    complete cleanly for any provider."""
    engine, company_id = enrichment_db
    with Session(engine) as session:
        run_id, _ = start_or_join_job(session, company_id, trigger="profile_view")

    broken_one = FakeProvider("broken_one", raises=RuntimeError("boom"))
    broken_two = FakeProvider("broken_two", raises=RuntimeError("also boom"))
    with Session(engine) as session:
        result = run_cascade_for_job(
            session, run_id, company_id, "Orchestrator Test Co Ltd", providers=(broken_one, broken_two)
        )

    assert result["status"] == "failed"
    assert result["fields_written"] == ()
    assert _job_status(engine, run_id) == "failed"


def test_a_finish_after_reclaim_reports_the_real_persisted_status_not_success(
    enrichment_db,
) -> None:
    """Pre-PR-review finding: run_cascade_for_job() used to unconditionally
    return status="success" even when this exact run_id had already been
    reclaimed (marked 'failed') by a concurrent start_or_join_job() call
    while this cascade was still in flight -- a lease-vs-real-latency
    race (only reachable today with an artificially short lease_ttl,
    since OrgBook's actual latency is sub-second against the 10-minute
    default; becomes practically reachable once Phase 3/6 add
    slower providers). _finish_job()'s own DB transition already correctly
    stayed 'failed' (idempotent no-op on an already-terminal row, mirroring
    pipeline.job_run.finish_job_run()'s exact precedent) -- what was wrong
    was the RETURNED dict claiming "success" regardless. Facts a late
    cascade gathers are still written (legitimate provider output,
    provenance-tracked under their own run_id) -- only the reported
    status was misleading, and that is what this test locks in."""
    from datetime import timedelta

    engine, company_id = enrichment_db
    with Session(engine) as session:
        slow_run_id, _ = start_or_join_job(
            session, company_id, trigger="profile_view", lease_ttl=timedelta(milliseconds=50)
        )
    time.sleep(0.1)  # the tiny lease genuinely expires

    with Session(engine) as session:
        new_run_id, joined = start_or_join_job(session, company_id, trigger="profile_view")
    assert joined is False
    assert new_run_id != slow_run_id  # reclaimed, not joined

    late_provider = FakeProvider(
        "stub", result=ProviderResult(provider="stub", matched=True, facts=(ProviderFact("legal_name", "Late Value", 0.9),))
    )
    with Session(engine) as session:
        late_result = run_cascade_for_job(
            session, slow_run_id, company_id, "Orchestrator Test Co Ltd", providers=(late_provider,)
        )

    assert late_result["status"] == "failed"  # NOT "success" -- matches the DB

    with engine.connect() as conn:
        db_status = conn.execute(
            text("SELECT status FROM company_enrichment_jobs WHERE run_id = :r"), {"r": slow_run_id}
        ).scalar_one()
    assert db_status == "failed"  # never resurrected to success


def test_verified_field_is_never_overwritten_by_an_automated_result(enrichment_db) -> None:
    """Golden case #8's core protection: a manually-verified field is a
    hard floor no automated provider result can cross, regardless of that
    provider's own confidence."""
    engine, company_id = enrichment_db
    with engine.begin() as conn:
        conn.execute(
            company_enrichment_fields.insert().values(
                company_id=company_id,
                field_name="legal_name",
                value="Manually Verified Name Ltd.",
                source="manual_verified",
                confidence=1.0,
                verified=True,
                fetched_at=datetime.now(timezone.utc),
                superseded_at=None,
                run_id=None,
            )
        )

    with Session(engine) as session:
        run_id, _ = start_or_join_job(session, company_id, trigger="agent")

    provider = FakeProvider(
        "orgbook",
        result=ProviderResult(
            provider="orgbook",
            matched=True,
            facts=(ProviderFact(field_name="legal_name", value="Automated Guess Inc.", confidence=0.95),),
        ),
    )
    with Session(engine) as session:
        result = run_cascade_for_job(
            session, run_id, company_id, "Orchestrator Test Co Ltd", providers=(provider,)
        )

    assert result["fields_written"] == ()
    assert result["fields_skipped_verified"] == ("legal_name",)

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT value, verified FROM company_enrichment_fields "
                "WHERE company_id = :id AND field_name = 'legal_name' AND superseded_at IS NULL"
            ),
            {"id": company_id},
        ).mappings().one()
    assert row["value"] == "Manually Verified Name Ltd."
    assert row["verified"] is True
