"""Company track-record shadow backfill orchestration (PR-G3.2).

Wires together the pure scorer (``pipeline.scoring.company_track_record``)
and the PR-G3.1 adapter/assignment helper (``pipeline.company_track_record``)
into a batch that reads real ``Company`` rows and writes the four
``track_record_*`` columns (migration 030, PR-G2B). This is the *only*
module in this PR that opens a session-owning loop, calls
``session.commit()``/``session.rollback()``, or selects which companies get
scored -- the adapter and assignment helper themselves still never touch a
session (see PR-G3.1).

Explicitly NOT wired into anything yet: no API route, no internal-steps
entry, no scheduler, no n8n workflow, and no existing enrichment function
calls this module. It is a standalone, manually-invokable entry point --
wiring it into any of those is out of scope for this PR.

Shadow-run contract (deliberately minimal for this first backfill PR --
no staleness window, no input-hash column):
  - default eligibility: ``track_record_version`` is NULL or differs from
    the scorer's current ``COMPANY_TRACK_RECORD_ALGORITHM_VERSION``.
  - ``force=True`` recomputes every selected company regardless of its
    current ``track_record_version``.
  - a second default run, immediately after a first run finished
    successfully, selects zero companies -- every row it touched now has
    the current version and is excluded by the eligibility filter.

Reference date / computed-at contract: both are computed at most once,
at the very top of ``backfill_company_track_records``, and threaded
through every company in the batch -- exactly the pattern already
established by ``pipeline.construction_tier.compute_construction_tiers``.
Neither the adapter nor the pure scorer ever reads the system clock.

Per-company outcome buckets (mutually exclusive, see docstring on
``backfill_company_track_records`` for the exact accounting):
  - ``persisted`` -- assignment + commit both succeeded (real runs only).
  - ``skipped`` -- adapter + scorer succeeded but persistence was
    intentionally never attempted (``dry_run=True`` only).
  - ``failed`` -- something raised (adapter, scorer, assignment, or
    commit) -- the session is rolled back and the batch continues.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from db.models import Company
from pipeline.company_track_record import (
    assign_track_record_result,
    build_company_track_record_input,
)
from pipeline.scoring.company_track_record import (
    COMPANY_TRACK_RECORD_ALGORITHM_VERSION,
    score_company_track_record,
)

__all__ = [
    "TrackRecordBackfillError",
    "STAGE_IDENTITY",
    "STAGE_ADAPTER",
    "STAGE_SCORER",
    "STAGE_ASSIGNMENT",
    "STAGE_COMMIT",
    "backfill_company_track_records",
]


class TrackRecordBackfillError(ValueError):
    """Raised for structurally invalid arguments to
    ``backfill_company_track_records`` itself (wrong-typed
    ``reference_date``/``computed_at``/``company_ids``/``sample_size``/
    ``dry_run``/``force``) -- raised once, up front, before any company is
    selected or scored, and before any session/DB access. Distinct from a
    per-company failure, which is caught, rolled back, and recorded in the
    run result's ``errors`` list instead of raised."""


# Fixed, closed set of per-company failure stages -- the only values that
# ever appear in a run result's errors[]["stage"]. Never derived from
# exception text. STAGE_IDENTITY covers the (rare) case where even reading
# company.id itself raises -- everything downstream of that read is
# necessarily attributed to a later stage.
STAGE_IDENTITY = "identity"
STAGE_ADAPTER = "adapter"
STAGE_SCORER = "scorer"
STAGE_ASSIGNMENT = "assignment"
STAGE_COMMIT = "commit"


def _validate_company_ids(company_ids: Any) -> list[int] | None:
    if company_ids is None:
        return None
    if not isinstance(company_ids, (list, tuple)):
        raise TrackRecordBackfillError(
            f"company_ids must be a list/tuple of int, got {type(company_ids).__name__}"
        )
    for item in company_ids:
        if isinstance(item, bool) or not isinstance(item, int):
            raise TrackRecordBackfillError(
                f"company_ids must contain only int values, got {item!r}"
            )
    return list(company_ids)


def _validate_sample_size(sample_size: Any) -> int | None:
    if sample_size is None:
        return None
    if isinstance(sample_size, bool) or not isinstance(sample_size, int):
        raise TrackRecordBackfillError(
            f"sample_size must be a plain int, got {type(sample_size).__name__}"
        )
    if sample_size < 0:
        raise TrackRecordBackfillError(f"sample_size must be >= 0, got {sample_size}")
    return sample_size


def _validate_bool_flag(name: str, value: Any) -> bool:
    """Fail-closed: ``type(value) is bool`` exactly -- rejects truthy/falsy
    stand-ins (``1``/``0``, ``"true"``, ``None``, etc.) that would
    otherwise silently coerce to a boolean via normal Python truthiness."""
    if type(value) is not bool:
        raise TrackRecordBackfillError(
            f"{name} must be a bool, got {type(value).__name__}"
        )
    return value


def _resolve_reference_date(reference_date: Any, *, now: datetime) -> date:
    if reference_date is None:
        return now.date()
    if not isinstance(reference_date, date) or isinstance(reference_date, datetime):
        raise TrackRecordBackfillError(
            "reference_date must be a datetime.date, not datetime.datetime, "
            f"got {reference_date!r}"
        )
    return reference_date


def _resolve_computed_at(computed_at: Any, *, now: datetime) -> datetime:
    """Resolve, and normalize to UTC, the single ``computed_at`` value used
    for the whole run. ``now`` (the caller's ``datetime.now(timezone.utc)``)
    is already UTC. A caller-supplied value must be timezone-aware; a
    non-UTC aware value is converted to UTC here, once -- the exact same
    normalized value is then passed to ``assign_track_record_result`` and
    reported back in the run result, so both can never disagree."""
    if computed_at is None:
        return now
    if not isinstance(computed_at, datetime):
        raise TrackRecordBackfillError(
            f"computed_at must be a datetime, got {type(computed_at).__name__}"
        )
    if computed_at.tzinfo is None:
        raise TrackRecordBackfillError(
            "computed_at must be timezone-aware, got a naive datetime"
        )
    return computed_at.astimezone(timezone.utc)


def _select_companies(
    session: Session,
    *,
    company_ids: list[int] | None,
    sample_size: int | None,
    force: bool,
) -> list[Company]:
    """Deterministic (ordered by ``Company.id``) selection of the batch a
    run will operate on.

    ``company_ids=None`` applies no ID filter at all; ``company_ids=[]``
    (an explicit, non-None empty list) selects zero rows -- these are
    deliberately different, not both treated as "no filter".
    ``sample_size`` is applied as a SQL ``LIMIT`` after the eligibility and
    ``company_ids`` filters and after ``ORDER BY``, per the PR-G3.2
    contract.

    Runs inside ``session.no_autoflush`` so this read-only selection query
    never triggers an autoflush of pending changes the *caller* may have
    staged on unrelated objects in the same session -- this function must
    never have a side effect on anything outside its own return value.
    """
    stmt = select(Company).order_by(Company.id)
    if not force:
        stmt = stmt.where(
            or_(
                Company.track_record_version.is_(None),
                Company.track_record_version != COMPANY_TRACK_RECORD_ALGORITHM_VERSION,
            )
        )
    if company_ids is not None:
        stmt = stmt.where(Company.id.in_(company_ids))
    if sample_size is not None:
        stmt = stmt.limit(sample_size)

    with session.no_autoflush:
        return list(session.scalars(stmt).all())


def backfill_company_track_records(
    session: Session,
    *,
    company_ids: list[int] | None = None,
    sample_size: int | None = None,
    reference_date: date | None = None,
    computed_at: datetime | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Compute and (unless ``dry_run``) persist ``company_track_record_v1``
    for the selected batch of companies.

    Selection: deterministic, ordered by ``Company.id``. By default only
    companies whose ``track_record_version`` is NULL or differs from
    ``COMPANY_TRACK_RECORD_ALGORITHM_VERSION`` are eligible;
    ``force=True`` recomputes every selected company regardless of its
    current version. ``company_ids`` further restricts the eligible set;
    ``sample_size`` truncates it afterward.

    ``reference_date`` and ``computed_at`` are each resolved exactly once,
    at the top of this function (UTC "now" by default, injectable for
    tests), and passed unchanged to every company in the batch -- neither
    the adapter nor the pure scorer ever reads the system clock.

    Per company: build the adapter input, call the pure scorer, and --
    unless ``dry_run`` -- call ``assign_track_record_result`` followed by
    exactly one ``session.commit()``. Any exception at any of these steps
    is caught, the session is rolled back (real runs only -- ``dry_run``
    never mutates the session in the first place, so there is nothing to
    roll back), a fail-closed, payload-free error (stage + exception type
    only -- never the exception message) is recorded, and the batch
    continues with the next company -- one company's failure never
    prevents any other company from being processed.

    ``dry_run=True`` runs the adapter and scorer for every selected
    company but never calls ``assign_track_record_result``, never mutates
    any ORM attribute, and never calls ``commit()``/``flush()``.

    A company whose core-evidence-free result has ``score=None`` is a
    successful, fully computed result -- not a failure -- and is
    persisted (or dry-run-counted) exactly like any other.

    Returns a dict with (at least): ``selected``, ``processed``,
    ``persisted``, ``skipped``, ``failed``, ``dry_run``,
    ``algorithm_version``, ``reference_date`` (ISO string),
    ``computed_at`` (ISO string, UTC), ``diagnostics_notes_count``,
    ``errors`` (a fail-closed list of ``{"company_id", "stage",
    "error_type"}`` dicts -- ``stage`` is always one of ``STAGE_ADAPTER``/
    ``STAGE_SCORER``/``STAGE_ASSIGNMENT``/``STAGE_COMMIT``, ``error_type``
    is always just ``type(exc).__name__``; neither the exception message,
    SQL parameters, connection details, nor any Company payload is ever
    included), and ``results`` (a per-company summary list: ``company_id``,
    ``score``, ``status``, ``diagnostics_notes``, ``coverage`` --
    ``TrackRecordCoverage.to_dict()``, already computed as part of scoring
    at zero extra cost -- added so a read-only reporting layer, e.g. a
    dry-run artifact aggregator, can summarize scorer input coverage
    without re-deriving the score itself; purely additive, changes
    neither selection nor persistence behavior).
    """
    company_ids = _validate_company_ids(company_ids)
    sample_size = _validate_sample_size(sample_size)
    dry_run = _validate_bool_flag("dry_run", dry_run)
    force = _validate_bool_flag("force", force)
    now = datetime.now(timezone.utc)
    reference_date = _resolve_reference_date(reference_date, now=now)
    computed_at = _resolve_computed_at(computed_at, now=now)

    companies = _select_companies(
        session, company_ids=company_ids, sample_size=sample_size, force=force
    )

    selected = len(companies)
    processed = 0
    persisted = 0
    skipped = 0
    failed = 0
    diagnostics_notes_count = 0
    errors: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    for company in companies:
        company_id: int | None = None
        stage = STAGE_IDENTITY
        try:
            # Reading .id itself is inside the protected block: an
            # identity-read failure (however unlikely for an
            # already-loaded ORM object) must not raise out of this loop
            # and abort the rest of the batch -- it is recorded exactly
            # like any other per-company failure, just with
            # company_id=None (there is nothing safe to report -- the
            # read that would have produced it is what failed).
            company_id = company.id
            stage = STAGE_ADAPTER
            adapter_result = build_company_track_record_input(
                company, reference_date=reference_date
            )
            diagnostics_notes_count += len(adapter_result.diagnostics.notes)

            stage = STAGE_SCORER
            scored = score_company_track_record(
                adapter_result.input, reference_date=reference_date
            )
            processed += 1

            if dry_run:
                skipped += 1
                results.append(
                    {
                        "company_id": company_id,
                        "score": scored.score,
                        "status": "dry_run_computed",
                        "diagnostics_notes": len(adapter_result.diagnostics.notes),
                        "coverage": scored.coverage.to_dict(),
                    }
                )
                continue

            stage = STAGE_ASSIGNMENT
            assign_track_record_result(company, scored, computed_at=computed_at)

            stage = STAGE_COMMIT
            session.commit()
            persisted += 1
            results.append(
                {
                    "company_id": company_id,
                    "score": scored.score,
                    "status": "persisted",
                    "diagnostics_notes": len(adapter_result.diagnostics.notes),
                    "coverage": scored.coverage.to_dict(),
                }
            )
        except Exception as exc:
            if not dry_run:
                session.rollback()
            failed += 1
            errors.append(
                {
                    "company_id": company_id,
                    "stage": stage,
                    "error_type": type(exc).__name__,
                }
            )

    return {
        "selected": selected,
        "processed": processed,
        "persisted": persisted,
        "skipped": skipped,
        "failed": failed,
        "dry_run": dry_run,
        "algorithm_version": COMPANY_TRACK_RECORD_ALGORITHM_VERSION,
        "reference_date": reference_date.isoformat(),
        "computed_at": computed_at.isoformat(),
        "diagnostics_notes_count": diagnostics_notes_count,
        "errors": errors,
        "results": results,
    }
