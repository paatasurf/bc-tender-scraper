"""Deterministic company track-record scorer (PR-G1).

Semantic contract
------------------
``score_company_track_record`` measures a company's demonstrated *public
operational track record* in BC's permit and public-award record --
volume of activity, how long that activity has been observed, and how
recently it was last observed -- optionally boosted by independently
sourced public reputation (Google) and public-buyer diversity when that
data happens to be available.

It does NOT measure, and must never be interpreted as measuring:
  - financial health or creditworthiness
  - safety or regulatory compliance record
  - litigation or dispute history
  - workmanship / build quality
  - fit for any specific tender (see pipeline.scoring.construction_match_scoring
    for that -- a different, tender-relative construct)

A ``None`` score means "no public track-record evidence at all was
supplied", not "zero reliability". A low score commonly reflects a newer
entrant or a company whose activity falls outside the two data sources
this scorer is built on (e.g. private-sector-only work), not evidence of
poor performance.

Determinism
-----------
This module performs no I/O, no randomness, no AI/LLM calls, and no
network access. The only external input besides ``CompanyTrackRecordInput``
is the caller-supplied ``reference_date`` -- there is no hidden
``date.today()``/``datetime.now()`` anywhere in this file, so the same
input + reference_date always produces byte-identical output.

Avoiding double counting
-------------------------
Permits and public-tender awards are two distinct evidence streams (a
municipal building permit is not the same record type as a public tender
award). This scorer does not establish or assume anything about
project-level overlap between the two streams -- it simply never merges
their raw counts into one combined "activity volume" before scoring.
Instead ``permit_depth`` and ``award_depth`` are two independently capped
breakdown factors, each scored on its own saturation curve against only
its own count. A company with 50 permits and 0 awards cannot reach the
same "depth" credit as one with 25 permits + 25 awards by having its
counts silently summed; the breakdown always shows exactly how much
credit came from which stream. Longevity and recency, by contrast, are
inherently about the *observed time span* of activity (not a count), so
they legitimately draw on dates from both streams via min/max, not sum.

The final ``score`` is defined as the literal sum of the returned
breakdown factors' points -- not computed independently and reconciled
after the fact -- and every factor's ``points`` is capped at its own
``max_points`` before summation, and the max_points across all factors
sum to exactly 100. This makes "sum(breakdown.points) == score" an
invariant of the definition itself, not something that needs a
clamp-and-absorb correction step.

Input validation
-----------------
``score_company_track_record`` validates ``CompanyTrackRecordInput``
fail-closed before scoring: malformed data raises
``InvalidCompanyTrackRecordInputError`` rather than being silently
clamped or coerced. Normalizing raw, possibly-dirty upstream data (e.g.
parsing permit date strings, deduplicating buyer names) is the
responsibility of a future adapter/caller layer, not this pure scorer.

Type validation runs first, before any value-level check: ``input_`` must
be exactly a ``CompanyTrackRecordInput``, ``reference_date`` and every
populated date field must be a ``datetime.date`` and specifically NOT a
``datetime.datetime`` (a ``datetime`` is a subclass of ``date`` in
Python, so this is checked explicitly rather than relying on
``isinstance(x, date)`` alone) and not a string. Any type violation
raises ``InvalidCompanyTrackRecordInputError`` -- never a bare
``AttributeError``/``TypeError`` from deeper inside the scoring logic.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from pipeline.scoring.explain import BreakdownFactor, build_reasons

# ---------------------------------------------------------------------------
# Algorithm version (PR-E1 convention: module constant + read-only property)
# ---------------------------------------------------------------------------

COMPANY_TRACK_RECORD_ALGORITHM_VERSION = "company_track_record_v1"

# ---------------------------------------------------------------------------
# Named weights / thresholds -- every number used below is defined here.
# ---------------------------------------------------------------------------

# -- Core factors (near-universal evidence; always computed once core
#    evidence exists at all). Max points sum to CORE_MAX_POINTS.
PERMIT_DEPTH_MAX_POINTS = 30
PERMIT_DEPTH_SATURATION_COUNT = 25  # 25+ permits => full permit_depth credit

AWARD_DEPTH_MAX_POINTS = 15
AWARD_DEPTH_SATURATION_COUNT = 10  # 10+ awards => full award_depth credit

LONGEVITY_MAX_POINTS = 20
LONGEVITY_SATURATION_YEARS = 8.0  # 8+ observed years => full longevity credit
LONGEVITY_MIN_TOTAL_RECORDS = 2  # fewer than this => a single event, no span credit

RECENCY_MAX_POINTS = 15
RECENCY_PLATEAU_DAYS = 730  # <=2 years since last activity => full credit
RECENCY_DECAY_END_DAYS = 1825  # >=5 years since last activity => floor credit
RECENCY_FLOOR_POINTS = 3  # never zero once *some* activity date is known

CORE_MAX_POINTS = (
    PERMIT_DEPTH_MAX_POINTS
    + AWARD_DEPTH_MAX_POINTS
    + LONGEVITY_MAX_POINTS
    + RECENCY_MAX_POINTS
)

# -- Optional bonus factors (low production coverage; additive only, never
#    a penalty when absent). Max points sum to BONUS_MAX_POINTS.
GOOGLE_BONUS_MAX_POINTS = 14
GOOGLE_REVIEW_VOLUME_SATURATION_COUNT = 25  # 25+ reviews => full volume confidence
GOOGLE_REVIEW_VOLUME_FLOOR_CONFIDENCE = 0.5  # confidence multiplier at 0 reviews

BUYER_DIVERSITY_BONUS_MAX_POINTS = 6
BUYER_DIVERSITY_BREADTH_MAX_POINTS = 4
BUYER_DIVERSITY_REPEAT_BONUS_POINTS = 2
BUYER_DIVERSITY_SATURATION_COUNT = 5  # 5+ distinct buyers => full breadth credit

BONUS_MAX_POINTS = GOOGLE_BONUS_MAX_POINTS + BUYER_DIVERSITY_BONUS_MAX_POINTS

TOTAL_MAX_POINTS = CORE_MAX_POINTS + BONUS_MAX_POINTS
assert TOTAL_MAX_POINTS == 100, "factor max_points must sum to exactly 100"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class InvalidCompanyTrackRecordInputError(ValueError):
    """Raised by fail-closed input validation in score_company_track_record.

    Never raised silently-recovered from inside this module -- invalid
    input always aborts scoring rather than being clamped or coerced.
    """


# ---------------------------------------------------------------------------
# Input / output contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompanyTrackRecordInput:
    """Immutable, ORM-independent input to the scorer.

    Date fields are already-parsed ``date`` objects, not raw strings --
    parsing raw permit/award date strings is a caller concern (the future
    pipeline-wiring PR), kept out of this pure-function layer.
    ``distinct_buyer_count`` is a pre-aggregated count, not raw buyer
    names -- this scorer never receives or handles company/buyer names.
    """

    total_projects: int = 0
    first_project_date: date | None = None
    last_project_date: date | None = None

    award_count: int = 0
    first_award_date: date | None = None
    last_award_date: date | None = None
    distinct_buyer_count: int = 0

    last_activity_at: date | None = None

    google_rating: float | None = None
    google_reviews_count: int | None = None


@dataclass(frozen=True)
class TrackRecordCoverage:
    """Data-completeness metadata, kept separate from the score itself."""

    core_evidence_present: bool
    has_permit_evidence: bool
    has_award_evidence: bool
    has_recency_signal: bool
    has_google_signal: bool
    has_buyer_diversity_signal: bool
    bonus_factors_present: int  # 0, 1, or 2 -- count of populated bonus factors

    def to_dict(self) -> dict[str, Any]:
        return {
            "core_evidence_present": self.core_evidence_present,
            "has_permit_evidence": self.has_permit_evidence,
            "has_award_evidence": self.has_award_evidence,
            "has_recency_signal": self.has_recency_signal,
            "has_google_signal": self.has_google_signal,
            "has_buyer_diversity_signal": self.has_buyer_diversity_signal,
            "bonus_factors_present": self.bonus_factors_present,
        }


@dataclass(frozen=True)
class CompanyTrackRecordResult:
    score: int | None
    breakdown: tuple[BreakdownFactor, ...]
    reasons: tuple[str, ...]
    coverage: TrackRecordCoverage
    reference_date: date

    @property
    def algorithm_version(self) -> str:
        return COMPANY_TRACK_RECORD_ALGORITHM_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "breakdown": [b.to_dict() for b in self.breakdown],
            "reasons": list(self.reasons),
            "coverage": self.coverage.to_dict(),
            "reference_date": self.reference_date.isoformat(),
            "algorithm_version": self.algorithm_version,
        }


def _factor(
    key: str, label: str, points: int, max_points: int, detail: str
) -> BreakdownFactor:
    return BreakdownFactor(
        factor=key, label=label, points=points, max_points=max_points, detail=detail
    )


# ---------------------------------------------------------------------------
# Core factors
# ---------------------------------------------------------------------------


def _log_scaled_points(count: int, *, max_points: int, saturation_count: int) -> int:
    if count <= 0:
        return 0
    ratio = math.log1p(count) / math.log1p(saturation_count)
    return min(max_points, round(max_points * ratio))


def _score_permit_depth(total_projects: int) -> BreakdownFactor:
    if total_projects <= 0:
        return _factor(
            "permit_depth",
            "Permit activity depth",
            0,
            PERMIT_DEPTH_MAX_POINTS,
            "No permit records on file -- no permit depth credit",
        )
    points = _log_scaled_points(
        total_projects,
        max_points=PERMIT_DEPTH_MAX_POINTS,
        saturation_count=PERMIT_DEPTH_SATURATION_COUNT,
    )
    return _factor(
        "permit_depth",
        "Permit activity depth",
        points,
        PERMIT_DEPTH_MAX_POINTS,
        f"{total_projects} permit record(s) on file",
    )


def _score_award_depth(award_count: int) -> BreakdownFactor:
    if award_count <= 0:
        return _factor(
            "award_depth",
            "Public award activity depth",
            0,
            AWARD_DEPTH_MAX_POINTS,
            "No public award records on file -- no award depth credit",
        )
    points = _log_scaled_points(
        award_count,
        max_points=AWARD_DEPTH_MAX_POINTS,
        saturation_count=AWARD_DEPTH_SATURATION_COUNT,
    )
    return _factor(
        "award_depth",
        "Public award activity depth",
        points,
        AWARD_DEPTH_MAX_POINTS,
        f"{award_count} public award record(s) on file",
    )


def _score_longevity(input_: CompanyTrackRecordInput) -> BreakdownFactor:
    total_records = input_.total_projects + input_.award_count
    first_candidates = [
        d for d in (input_.first_project_date, input_.first_award_date) if d is not None
    ]
    last_candidates = [
        d for d in (input_.last_project_date, input_.last_award_date) if d is not None
    ]

    if total_records < LONGEVITY_MIN_TOTAL_RECORDS:
        return _factor(
            "longevity",
            "Observed activity span",
            0,
            LONGEVITY_MAX_POINTS,
            "Fewer than 2 records on file -- a single event is not evidence of a multi-year history",
        )

    if not first_candidates or not last_candidates:
        return _factor(
            "longevity",
            "Observed activity span",
            0,
            LONGEVITY_MAX_POINTS,
            "No usable first/last activity dates -- no span credit",
        )

    earliest = min(first_candidates)
    latest = max(last_candidates)
    span_days = (latest - earliest).days

    if span_days <= 0:
        return _factor(
            "longevity",
            "Observed activity span",
            0,
            LONGEVITY_MAX_POINTS,
            "No observed span between first and last recorded activity",
        )

    span_years = span_days / 365.25
    ratio = min(1.0, span_years / LONGEVITY_SATURATION_YEARS)
    points = min(LONGEVITY_MAX_POINTS, round(LONGEVITY_MAX_POINTS * ratio))
    return _factor(
        "longevity",
        "Observed activity span",
        points,
        LONGEVITY_MAX_POINTS,
        f"{span_years:.1f} years observed between first and last recorded activity ({total_records} records)",
    )


def _most_recent_activity_date(input_: CompanyTrackRecordInput) -> date | None:
    candidates = [
        d
        for d in (
            input_.last_project_date,
            input_.last_award_date,
            input_.last_activity_at,
        )
        if d is not None
    ]
    return max(candidates) if candidates else None


def _score_recency(
    input_: CompanyTrackRecordInput, *, reference_date: date
) -> BreakdownFactor:
    most_recent = _most_recent_activity_date(input_)
    if most_recent is None:
        return _factor(
            "recency",
            "Recency of most recent activity",
            0,
            RECENCY_MAX_POINTS,
            "No activity date available -- no recency credit",
        )

    age_days = max(0, (reference_date - most_recent).days)

    if age_days <= RECENCY_PLATEAU_DAYS:
        return _factor(
            "recency",
            "Recency of most recent activity",
            RECENCY_MAX_POINTS,
            RECENCY_MAX_POINTS,
            f"{age_days} days since most recent activity -- within plateau window (<={RECENCY_PLATEAU_DAYS} days), full credit",
        )

    if age_days >= RECENCY_DECAY_END_DAYS:
        return _factor(
            "recency",
            "Recency of most recent activity",
            RECENCY_FLOOR_POINTS,
            RECENCY_MAX_POINTS,
            f"{age_days} days since most recent activity -- beyond decay window (>={RECENCY_DECAY_END_DAYS} days), floor credit retained",
        )

    fraction = (age_days - RECENCY_PLATEAU_DAYS) / (
        RECENCY_DECAY_END_DAYS - RECENCY_PLATEAU_DAYS
    )
    points = round(
        RECENCY_MAX_POINTS - fraction * (RECENCY_MAX_POINTS - RECENCY_FLOOR_POINTS)
    )
    points = max(RECENCY_FLOOR_POINTS, min(RECENCY_MAX_POINTS, points))
    return _factor(
        "recency",
        "Recency of most recent activity",
        points,
        RECENCY_MAX_POINTS,
        f"{age_days} days since most recent activity -- decaying credit",
    )


# ---------------------------------------------------------------------------
# Optional bonus factors -- additive only, 0 (never a penalty) when absent.
# ---------------------------------------------------------------------------


def _score_google_reputation(input_: CompanyTrackRecordInput) -> BreakdownFactor:
    if input_.google_rating is None:
        return _factor(
            "google_reputation",
            "Google public reputation",
            0,
            GOOGLE_BONUS_MAX_POINTS,
            "No Google rating available -- no bonus credit (absence is not penalized)",
        )

    rating = max(0.0, min(5.0, input_.google_rating))
    reviews = input_.google_reviews_count or 0
    confidence = GOOGLE_REVIEW_VOLUME_FLOOR_CONFIDENCE + (
        1.0 - GOOGLE_REVIEW_VOLUME_FLOOR_CONFIDENCE
    ) * min(1.0, reviews / GOOGLE_REVIEW_VOLUME_SATURATION_COUNT)
    raw_points = GOOGLE_BONUS_MAX_POINTS * (rating / 5.0) * confidence
    points = max(0, min(GOOGLE_BONUS_MAX_POINTS, round(raw_points)))
    return _factor(
        "google_reputation",
        "Google public reputation",
        points,
        GOOGLE_BONUS_MAX_POINTS,
        f"Google rating {rating}/5 from {reviews} review(s)",
    )


def _score_buyer_diversity(input_: CompanyTrackRecordInput) -> BreakdownFactor:
    if input_.distinct_buyer_count <= 0 or input_.award_count <= 0:
        return _factor(
            "buyer_diversity",
            "Public buyer diversity",
            0,
            BUYER_DIVERSITY_BONUS_MAX_POINTS,
            "No award/buyer data available -- no bonus credit (absence is not penalized)",
        )

    breadth_points = min(
        BUYER_DIVERSITY_BREADTH_MAX_POINTS,
        round(
            BUYER_DIVERSITY_BREADTH_MAX_POINTS
            * input_.distinct_buyer_count
            / BUYER_DIVERSITY_SATURATION_COUNT
        ),
    )
    repeat_detected = input_.award_count > input_.distinct_buyer_count
    repeat_points = BUYER_DIVERSITY_REPEAT_BONUS_POINTS if repeat_detected else 0
    points = min(BUYER_DIVERSITY_BONUS_MAX_POINTS, breadth_points + repeat_points)

    detail = f"{input_.distinct_buyer_count} distinct public buyer(s)"
    if repeat_detected:
        detail += ", repeat business detected"
    return _factor(
        "buyer_diversity",
        "Public buyer diversity",
        points,
        BUYER_DIVERSITY_BONUS_MAX_POINTS,
        detail,
    )


# ---------------------------------------------------------------------------
# Fail-closed input validation -- invalid input raises, never gets clamped
# or silently coerced. Normalizing raw upstream data is a caller concern.
# ---------------------------------------------------------------------------


_DATE_FIELDS = (
    "first_project_date",
    "last_project_date",
    "first_award_date",
    "last_award_date",
    "last_activity_at",
)


def _validate_date_type(name: str, value: object) -> None:
    """Reject anything that is not exactly a ``datetime.date``.

    ``datetime.datetime`` is a subclass of ``datetime.date`` in Python,
    so a plain ``isinstance(value, date)`` check would silently accept a
    datetime. It is rejected explicitly, with its own message, before the
    general ``date`` check runs.
    """
    if isinstance(value, datetime):
        raise InvalidCompanyTrackRecordInputError(
            f"{name} must be a datetime.date, not datetime.datetime, got {value!r}"
        )
    if not isinstance(value, date):
        raise InvalidCompanyTrackRecordInputError(
            f"{name} must be a datetime.date, got {type(value).__name__}"
        )


def _validate_types(input_: CompanyTrackRecordInput, *, reference_date: object) -> None:
    """Type-level validation -- runs before any attribute access or value
    check, so a wrong-typed input_ or reference_date raises
    InvalidCompanyTrackRecordInputError, never a bare
    AttributeError/TypeError from deeper inside scoring."""
    if type(input_) is not CompanyTrackRecordInput:
        raise InvalidCompanyTrackRecordInputError(
            f"input_ must be a CompanyTrackRecordInput, got {type(input_).__name__}"
        )
    _validate_date_type("reference_date", reference_date)

    for name in _DATE_FIELDS:
        value = getattr(input_, name)
        if value is not None:
            _validate_date_type(name, value)


def _validate_plain_count(name: str, value: int) -> None:
    if type(value) is not int:
        raise InvalidCompanyTrackRecordInputError(
            f"{name} must be a plain int, got {type(value).__name__}"
        )
    if value < 0:
        raise InvalidCompanyTrackRecordInputError(f"{name} must be >= 0, got {value}")


def _validate_optional_count(name: str, value: int | None) -> None:
    if value is None:
        return
    _validate_plain_count(name, value)


def _validate_date_ordering(
    first_name: str, first: date | None, last_name: str, last: date | None
) -> None:
    if first is not None and last is not None and first > last:
        raise InvalidCompanyTrackRecordInputError(
            f"{first_name} ({first}) must be <= {last_name} ({last})"
        )


def _validate_not_future(name: str, value: date | None, reference_date: date) -> None:
    if value is not None and value > reference_date:
        raise InvalidCompanyTrackRecordInputError(
            f"{name} ({value}) must not be after reference_date ({reference_date})"
        )


def _validate_input(input_: CompanyTrackRecordInput, *, reference_date: date) -> None:
    _validate_plain_count("total_projects", input_.total_projects)
    _validate_plain_count("award_count", input_.award_count)
    _validate_plain_count("distinct_buyer_count", input_.distinct_buyer_count)
    _validate_optional_count("google_reviews_count", input_.google_reviews_count)

    if input_.distinct_buyer_count > input_.award_count:
        raise InvalidCompanyTrackRecordInputError(
            f"distinct_buyer_count ({input_.distinct_buyer_count}) must be "
            f"<= award_count ({input_.award_count})"
        )

    if input_.google_rating is not None:
        rating = input_.google_rating
        if isinstance(rating, bool) or not isinstance(rating, (int, float)):
            raise InvalidCompanyTrackRecordInputError(
                f"google_rating must be a real number, got {type(rating).__name__}"
            )
        if not math.isfinite(rating):
            raise InvalidCompanyTrackRecordInputError("google_rating must be finite")
        if not (0.0 <= rating <= 5.0):
            raise InvalidCompanyTrackRecordInputError(
                f"google_rating must be within 0..5, got {rating}"
            )

    _validate_date_ordering(
        "first_project_date",
        input_.first_project_date,
        "last_project_date",
        input_.last_project_date,
    )
    _validate_date_ordering(
        "first_award_date",
        input_.first_award_date,
        "last_award_date",
        input_.last_award_date,
    )

    for name, value in (
        ("first_project_date", input_.first_project_date),
        ("last_project_date", input_.last_project_date),
        ("first_award_date", input_.first_award_date),
        ("last_award_date", input_.last_award_date),
        ("last_activity_at", input_.last_activity_at),
    ):
        _validate_not_future(name, value, reference_date)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _has_core_evidence(input_: CompanyTrackRecordInput) -> bool:
    return input_.total_projects > 0 or input_.award_count > 0


def score_company_track_record(
    input_: CompanyTrackRecordInput,
    *,
    reference_date: date,
) -> CompanyTrackRecordResult:
    """Score a company's public operational track record.

    ``reference_date`` must be supplied explicitly by the caller -- this
    function never reads the system clock, so identical
    (input_, reference_date) pairs always produce identical output.

    Raises ``InvalidCompanyTrackRecordInputError`` (fail-closed, before
    any scoring happens) if ``input_`` fails validation -- see the module
    docstring's "Input validation" section.
    """
    _validate_types(input_, reference_date=reference_date)
    _validate_input(input_, reference_date=reference_date)

    has_google_signal = input_.google_rating is not None
    has_buyer_diversity_signal = (
        input_.distinct_buyer_count > 0 and input_.award_count > 0
    )
    bonus_factors_present = sum([has_google_signal, has_buyer_diversity_signal])
    has_recency_signal = _most_recent_activity_date(input_) is not None

    if not _has_core_evidence(input_):
        return CompanyTrackRecordResult(
            score=None,
            breakdown=(),
            reasons=(),
            coverage=TrackRecordCoverage(
                core_evidence_present=False,
                has_permit_evidence=input_.total_projects > 0,
                has_award_evidence=input_.award_count > 0,
                has_recency_signal=has_recency_signal,
                has_google_signal=has_google_signal,
                has_buyer_diversity_signal=has_buyer_diversity_signal,
                bonus_factors_present=bonus_factors_present,
            ),
            reference_date=reference_date,
        )

    permit_depth = _score_permit_depth(input_.total_projects)
    award_depth = _score_award_depth(input_.award_count)
    longevity = _score_longevity(input_)
    recency = _score_recency(input_, reference_date=reference_date)
    google_reputation = _score_google_reputation(input_)
    buyer_diversity = _score_buyer_diversity(input_)

    breakdown = (
        permit_depth,
        award_depth,
        longevity,
        recency,
        google_reputation,
        buyer_diversity,
    )
    score = max(0, min(TOTAL_MAX_POINTS, sum(f.points for f in breakdown)))

    coverage = TrackRecordCoverage(
        core_evidence_present=True,
        has_permit_evidence=input_.total_projects > 0,
        has_award_evidence=input_.award_count > 0,
        has_recency_signal=has_recency_signal,
        has_google_signal=has_google_signal,
        has_buyer_diversity_signal=has_buyer_diversity_signal,
        bonus_factors_present=bonus_factors_present,
    )

    reasons = tuple(build_reasons(list(breakdown), limit=5))

    return CompanyTrackRecordResult(
        score=score,
        breakdown=breakdown,
        reasons=reasons,
        coverage=coverage,
        reference_date=reference_date,
    )
