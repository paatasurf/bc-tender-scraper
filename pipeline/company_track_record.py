"""Adapter + assignment helper wiring pipeline.scoring.company_track_record
into Company ORM rows (PR-G3.1).

This module is the boundary between the pure, ORM-independent scorer
(``pipeline.scoring.company_track_record``) and ``db.models.Company``. It
performs no I/O of its own:

  - ``build_company_track_record_input`` takes an already-loaded ``Company``
    (or any duck-typed object exposing the same attributes -- no
    ``isinstance`` check against ``db.models.Company`` is performed, and
    this module never imports ``db.models``) and returns a scorer-ready
    ``CompanyTrackRecordInput`` plus an immutable diagnostics record of
    anything that was normalized away.

  - ``assign_track_record_result`` takes an already-computed
    ``CompanyTrackRecordResult`` and mutates exactly the four
    ``track_record_*`` attributes on a company instance.

Neither function opens a DB session, issues SQL, commits/rolls back a
transaction, calls an external API/LLM, or reads the system clock.
Session lifecycle, transaction boundaries, and company selection belong
entirely to a future orchestration/backfill layer (PR-G3.2), not here.

Normalization philosophy
-------------------------
Two different failure modes are handled differently, deliberately:

  - ``total_projects`` / ``award_count`` are treated as *structural*
    fields -- a value that is not a plain, non-negative ``int`` (or
    ``None``, treated as 0) indicates the caller handed this adapter
    something that was never a valid Company in the first place, so
    ``build_company_track_record_input`` raises
    ``CompanyTrackRecordAdapterError`` rather than guessing.

  - Everything else that can plausibly arrive dirty from upstream data
    (date strings, ``last_activity_at``, ``award_clients``, Google
    fields) is normalized *fail-open*: a malformed value is dropped
    (mapped to ``None`` / excluded), a note is appended to the returned
    diagnostics, and building continues. This guarantees
    ``build_company_track_record_input`` never hands
    ``score_company_track_record`` an input that would make it raise
    ``InvalidCompanyTrackRecordInputError`` for a data-quality reason --
    the pure scorer's own docstring is explicit that normalizing raw,
    possibly-dirty upstream data is an adapter concern, not its own.

This module never reads ``ai_reliability_score``, ``ai_summary``,
``construction_score``, ``construction_tier_json``, ``cip_json``,
``capability_profile_json``, or any other AI/derived-scoring column --
only the handful of raw permit/award/Google attributes the pure scorer's
``CompanyTrackRecordInput`` actually needs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from pipeline.scoring.company_track_record import (
    CompanyTrackRecordInput,
    CompanyTrackRecordResult,
)

__all__ = [
    "CompanyTrackRecordAdapterError",
    "TrackRecordAdapterDiagnostics",
    "TrackRecordAdapterResult",
    "build_company_track_record_input",
    "WRITABLE_TRACK_RECORD_COLUMNS",
    "assign_track_record_result",
]


class CompanyTrackRecordAdapterError(ValueError):
    """Raised for structurally invalid input this adapter cannot safely
    normalize -- a wrong-typed ``reference_date``/``computed_at``, a
    negative or non-numeric count, a non-``CompanyTrackRecordResult``
    result, or an attempted write outside ``WRITABLE_TRACK_RECORD_COLUMNS``.
    Distinct from the fail-open normalization applied to dates,
    ``award_clients``, and Google fields, which are dropped with a
    diagnostic note instead of raising.
    """


# ---------------------------------------------------------------------------
# Diagnostics contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrackRecordAdapterDiagnostics:
    """Immutable record of anything ``build_company_track_record_input``
    normalized away. Purely observational -- has no effect on scoring and
    is never consulted by the scorer itself."""

    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"notes": list(self.notes)}


@dataclass(frozen=True)
class TrackRecordAdapterResult:
    """Return value of ``build_company_track_record_input``: the
    scorer-ready input, plus what was dropped/normalized to produce it."""

    input: CompanyTrackRecordInput
    diagnostics: TrackRecordAdapterDiagnostics


# ---------------------------------------------------------------------------
# Field-level normalization helpers
# ---------------------------------------------------------------------------


def _coerce_nonnegative_count(name: str, value: Any) -> int:
    """Structural validation -- raises, never clamps, on anything that
    isn't unambiguously a valid count. ``None`` is treated as 0 (a
    genuinely absent count, not a malformed one)."""
    if value is None:
        return 0
    if isinstance(value, bool):
        raise CompanyTrackRecordAdapterError(
            f"{name} must be a plain int, got bool ({value!r})"
        )
    if not isinstance(value, int):
        raise CompanyTrackRecordAdapterError(
            f"{name} must be a plain int, got {type(value).__name__} ({value!r})"
        )
    if value < 0:
        raise CompanyTrackRecordAdapterError(f"{name} must be >= 0, got {value}")
    return value


def _parse_date_string(name: str, value: Any, notes: list[str]) -> date | None:
    """Single local ISO-date parser reused for all four Company date-string
    fields (``first_project_date``, ``last_project_date``,
    ``first_award_date``, ``last_award_date``). Mirrors the fail-open
    ``date.fromisoformat(value[:10])`` idiom already used throughout this
    codebase (e.g. ``pipeline/construction_tier.py::_parse_date``): empty
    string / None is the normal "unset" sentinel (silently None, no
    diagnostic); a non-empty, unparseable string is malformed (None +
    diagnostic note)."""
    if value is None:
        return None
    if not isinstance(value, str):
        notes.append(
            f"{name}: expected a date string, got {type(value).__name__} -- dropped"
        )
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        notes.append(f"{name}: could not parse {value!r} as an ISO date -- dropped")
        return None


def _normalize_last_activity_at(value: Any, notes: list[str]) -> date | None:
    """``Company.last_activity_at`` is a tz-aware ``DateTime`` column --
    convert to a UTC ``date`` for the scorer. A naive ``datetime`` cannot be
    safely reinterpreted as UTC (the true offset is unknown), so it is
    dropped with a diagnostic rather than guessed at."""
    if value is None:
        return None
    if not isinstance(value, datetime):
        notes.append(
            f"last_activity_at: expected a datetime, got {type(value).__name__} -- dropped"
        )
        return None
    if value.tzinfo is None:
        notes.append(
            "last_activity_at: naive datetime cannot be safely converted to a UTC date -- dropped"
        )
        return None
    return value.astimezone(timezone.utc).date()


def _normalize_award_clients(value: Any, notes: list[str]) -> set[str]:
    """Normalize ``Company.award_clients`` (an ``ARRAY(String)``, already
    deduplicated by its only production writer but capped at 15 entries --
    see PR-G3 discovery report) into a clean set of unique, non-empty,
    stripped strings. ``None`` and an empty list/tuple both normalize to an
    empty set with no diagnostic (that is the ordinary "no award data"
    case); non-string elements and empty/whitespace-only strings within a
    valid list/tuple are dropped with a diagnostic -- this is defensive
    against legacy/malformed payloads, not the expected shape of current
    writes.

    Only ``list``/``tuple`` are accepted as the container itself. A
    ``str``/``bytes`` payload is iterable but must never be treated as a
    sequence of client names here -- iterating it would silently walk
    individual characters/bytes instead. Any other non-``None`` value that
    is not a ``list``/``tuple`` (``str``, ``bytes``, ``int``, ``dict``, an
    arbitrary object, ...) is never iterated at all: it is treated as an
    empty container, with a diagnostic recording the actual type, and this
    function never raises ``TypeError``."""
    if value is None:
        return set()
    if not isinstance(value, (list, tuple)):
        notes.append(
            f"award_clients: expected a list/tuple, got {type(value).__name__} "
            "-- not iterated, treated as empty"
        )
        return set()
    if not value:
        return set()

    unique: set[str] = set()
    dropped_malformed = 0
    dropped_empty = 0
    for item in value:
        if not isinstance(item, str):
            dropped_malformed += 1
            continue
        cleaned = item.strip()
        if not cleaned:
            dropped_empty += 1
            continue
        unique.add(cleaned)

    if dropped_malformed:
        notes.append(
            f"award_clients: dropped {dropped_malformed} non-string element(s)"
        )
    if dropped_empty:
        notes.append(
            f"award_clients: dropped {dropped_empty} empty/whitespace-only element(s)"
        )
    return unique


def _normalize_google_rating(value: Any, notes: list[str]) -> float | None:
    """Fail-open normalization matching the scorer's own fail-closed
    ``google_rating`` contract (real number, finite, within 0..5) -- a
    value that would make the scorer raise is dropped here instead, with a
    diagnostic, so the scorer only ever sees a value it will accept."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        notes.append(
            f"google_rating: expected a real number, got {type(value).__name__} -- dropped"
        )
        return None
    rating = float(value)
    if not math.isfinite(rating):
        notes.append("google_rating: non-finite value -- dropped")
        return None
    if not (0.0 <= rating <= 5.0):
        notes.append(f"google_rating: {rating} outside 0..5 -- dropped")
        return None
    return rating


def _normalize_google_reviews_count(value: Any, notes: list[str]) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        notes.append(
            f"google_reviews_count: expected an int, got {type(value).__name__} -- dropped"
        )
        return None
    if value < 0:
        notes.append(f"google_reviews_count: negative value {value} -- dropped")
        return None
    return value


def _drop_if_future(
    name: str, value: date | None, reference_date: date, notes: list[str]
) -> date | None:
    """The scorer fail-closed rejects any populated date after
    ``reference_date``. Dropping it here (fail-open, with a diagnostic)
    instead means a stray future-dated record never aborts scoring for the
    whole company."""
    if value is not None and value > reference_date:
        notes.append(
            f"{name}: {value.isoformat()} is after reference_date "
            f"{reference_date.isoformat()} -- dropped"
        )
        return None
    return value


def _drop_if_misordered(
    first_name: str,
    first: date | None,
    last_name: str,
    last: date | None,
    notes: list[str],
) -> tuple[date | None, date | None]:
    """The scorer fail-closed rejects first > last for a date pair. Both
    values are dropped together here (fail-open) rather than guessing
    which one is wrong."""
    if first is not None and last is not None and first > last:
        notes.append(
            f"{first_name} ({first.isoformat()}) is after {last_name} "
            f"({last.isoformat()}) -- both dropped"
        )
        return None, None
    return first, last


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


def build_company_track_record_input(
    company: Any,
    *,
    reference_date: date,
) -> TrackRecordAdapterResult:
    """Build a scorer-ready ``CompanyTrackRecordInput`` from a Company ORM
    row (or any duck-typed object exposing the same attributes).

    Performs no DB queries, no session access, no network/API calls, no
    LLM calls, and never reads the system clock -- ``reference_date`` must
    be supplied explicitly by the caller, exactly once per batch/run (see
    PR-G3 discovery report, section 5).

    Raises ``CompanyTrackRecordAdapterError`` for a wrong-typed
    ``reference_date`` or a structurally invalid ``total_projects`` /
    ``award_count``. Everything else that arrives malformed is normalized
    away and recorded in the returned diagnostics -- see the module
    docstring's "Normalization philosophy" section.
    """
    if not isinstance(reference_date, date) or isinstance(reference_date, datetime):
        raise CompanyTrackRecordAdapterError(
            "reference_date must be a datetime.date, not datetime.datetime, "
            f"got {reference_date!r}"
        )

    notes: list[str] = []

    total_projects = _coerce_nonnegative_count(
        "total_projects", getattr(company, "total_projects", None)
    )
    award_count = _coerce_nonnegative_count(
        "award_count", getattr(company, "award_count", None)
    )

    first_project_date = _parse_date_string(
        "first_project_date", getattr(company, "first_project_date", None), notes
    )
    last_project_date = _parse_date_string(
        "last_project_date", getattr(company, "last_project_date", None), notes
    )
    first_award_date = _parse_date_string(
        "first_award_date", getattr(company, "first_award_date", None), notes
    )
    last_award_date = _parse_date_string(
        "last_award_date", getattr(company, "last_award_date", None), notes
    )
    last_activity_at = _normalize_last_activity_at(
        getattr(company, "last_activity_at", None), notes
    )

    first_project_date = _drop_if_future(
        "first_project_date", first_project_date, reference_date, notes
    )
    last_project_date = _drop_if_future(
        "last_project_date", last_project_date, reference_date, notes
    )
    first_award_date = _drop_if_future(
        "first_award_date", first_award_date, reference_date, notes
    )
    last_award_date = _drop_if_future(
        "last_award_date", last_award_date, reference_date, notes
    )
    last_activity_at = _drop_if_future(
        "last_activity_at", last_activity_at, reference_date, notes
    )

    first_project_date, last_project_date = _drop_if_misordered(
        "first_project_date",
        first_project_date,
        "last_project_date",
        last_project_date,
        notes,
    )
    first_award_date, last_award_date = _drop_if_misordered(
        "first_award_date", first_award_date, "last_award_date", last_award_date, notes
    )

    unique_clients = _normalize_award_clients(
        getattr(company, "award_clients", None), notes
    )
    distinct_buyer_count = min(len(unique_clients), award_count)
    if len(unique_clients) > award_count:
        notes.append(
            f"distinct_buyer_count capped at award_count ({award_count}); "
            f"{len(unique_clients)} unique award_clients on file"
        )

    google_rating = _normalize_google_rating(
        getattr(company, "google_rating", None), notes
    )
    google_reviews_count = _normalize_google_reviews_count(
        getattr(company, "google_reviews_count", None), notes
    )

    input_ = CompanyTrackRecordInput(
        total_projects=total_projects,
        first_project_date=first_project_date,
        last_project_date=last_project_date,
        award_count=award_count,
        first_award_date=first_award_date,
        last_award_date=last_award_date,
        distinct_buyer_count=distinct_buyer_count,
        last_activity_at=last_activity_at,
        google_rating=google_rating,
        google_reviews_count=google_reviews_count,
    )
    return TrackRecordAdapterResult(
        input=input_, diagnostics=TrackRecordAdapterDiagnostics(notes=tuple(notes))
    )


# ---------------------------------------------------------------------------
# Assignment helper
# ---------------------------------------------------------------------------

# Regression guard, mirroring pipeline/google_enrichment/writer.py's
# WRITABLE_GOOGLE_COLUMNS/FORBIDDEN_COLUMNS pattern: the only four columns
# assign_track_record_result is ever allowed to write.
WRITABLE_TRACK_RECORD_COLUMNS: frozenset[str] = frozenset(
    {
        "track_record_score",
        "track_record_json",
        "track_record_at",
        "track_record_version",
    }
)


def assign_track_record_result(
    company: Any,
    result: CompanyTrackRecordResult,
    *,
    computed_at: datetime,
) -> None:
    """Assign exactly the four ``track_record_*`` attributes on ``company``
    from an already-computed ``CompanyTrackRecordResult``.

    Never opens a session, never calls ``commit()``/``rollback()``/
    ``flush()``, never issues SQL -- transaction ownership belongs entirely
    to a future orchestration layer (PR-G3.2).

    ``computed_at`` must be an explicit, timezone-aware ``datetime``
    supplied by the caller (never read from the system clock here); a
    naive ``datetime`` raises ``CompanyTrackRecordAdapterError``, and an
    aware value in a non-UTC zone is normalized to UTC before assignment.

    Always sets all four columns together, even when ``result.score`` is
    ``None`` -- this unconditionally satisfies migration 030's
    state-coherence CHECK constraint's "computed" branch
    (``track_record_json``/``track_record_at``/``track_record_version``
    all NOT NULL; ``track_record_score`` may independently be NULL)
    whenever this function is called at all.
    """
    if not isinstance(result, CompanyTrackRecordResult):
        raise CompanyTrackRecordAdapterError(
            f"result must be a CompanyTrackRecordResult, got {type(result).__name__}"
        )
    if not isinstance(computed_at, datetime):
        raise CompanyTrackRecordAdapterError(
            f"computed_at must be a datetime, got {type(computed_at).__name__}"
        )
    if computed_at.tzinfo is None:
        raise CompanyTrackRecordAdapterError(
            "computed_at must be timezone-aware, got a naive datetime"
        )
    computed_at_utc = computed_at.astimezone(timezone.utc)

    payload: dict[str, Any] = {
        "track_record_score": result.score,
        "track_record_json": result.to_dict(),
        "track_record_at": computed_at_utc,
        "track_record_version": result.algorithm_version,
    }
    unknown = set(payload) - WRITABLE_TRACK_RECORD_COLUMNS
    if unknown:
        raise CompanyTrackRecordAdapterError(
            f"assign_track_record_result attempted to write disallowed column(s): {sorted(unknown)}"
        )

    company.track_record_score = payload["track_record_score"]
    company.track_record_json = payload["track_record_json"]
    company.track_record_at = payload["track_record_at"]
    company.track_record_version = payload["track_record_version"]
