"""Shared, safe classification of raw exception/error text into a fixed,
non-parameterized label.

Used by pipeline/ops_read_model.py (M1/M2B, for pipeline_runs.error and
pipeline_coordinator_runs.error) and pipeline/job_run.py (M3B, for
ops_job_runs.error_summary). Extracted into its own pure module
specifically so neither caller carries its own copy of this
security-critical logic -- one classifier, one set of tests, one place to
audit.

error_summary is always one of a fixed set of labels below -- never a
substring of the original text -- so a raw error containing a connection
string, bearer token, or API key can never leak into any API response or
persisted database row, regardless of which category it happens to
match.
"""

from __future__ import annotations

import re

_ERROR_SUMMARY_TIMEOUT = "timeout"
_ERROR_SUMMARY_HTTP_4XX = "http_4xx"
_ERROR_SUMMARY_HTTP_5XX = "http_5xx"
_ERROR_SUMMARY_DATABASE = "database"
_ERROR_SUMMARY_VALIDATION = "validation"
_ERROR_SUMMARY_UNKNOWN = "unknown"

# The complete, closed set of values classify_run_error() can ever return
# as error_summary -- exported so callers (and tests) can validate against
# it without duplicating the literal list.
ERROR_SUMMARY_LABELS: frozenset[str] = frozenset(
    {
        _ERROR_SUMMARY_TIMEOUT,
        _ERROR_SUMMARY_HTTP_4XX,
        _ERROR_SUMMARY_HTTP_5XX,
        _ERROR_SUMMARY_DATABASE,
        _ERROR_SUMMARY_VALIDATION,
        _ERROR_SUMMARY_UNKNOWN,
    }
)

_TIMEOUT_MARKERS = ("timeout", "timed out")
_HTTP_5XX_RE = re.compile(r"\b5\d{2}\b")
_HTTP_4XX_RE = re.compile(r"\b4\d{2}\b")
_DATABASE_MARKERS = (
    "database",
    "postgres",
    "psycopg",
    "sqlalchemy",
    "connection refused",
    "relation does not exist",
    "integrityerror",
    "operationalerror",
    "could not connect",
)
_VALIDATION_MARKERS = (
    "validation",
    "valueerror",
    "keyerror",
    "typeerror",
    "required field",
    "invalid ",
)


def classify_run_error(raw: str | None) -> tuple[bool, str | None]:
    """Classify a raw error/exception string into (error_present,
    error_summary) WITHOUT ever returning any part of the original text.

    error_summary is always one of: timeout, http_4xx, http_5xx, database,
    validation, unknown -- a fixed label, never interpolated from the
    input. Classification is keyword-matching on a lowercased copy of the
    input purely to pick a label; that lowercased copy itself is
    discarded and never part of the return value. Checked in this order
    (most specific/actionable first): timeout, http 5xx, http 4xx,
    database, validation, else unknown.
    """
    if not raw:
        return False, None

    lowered = str(raw).lower()

    if any(marker in lowered for marker in _TIMEOUT_MARKERS):
        return True, _ERROR_SUMMARY_TIMEOUT
    if _HTTP_5XX_RE.search(lowered):
        return True, _ERROR_SUMMARY_HTTP_5XX
    if _HTTP_4XX_RE.search(lowered):
        return True, _ERROR_SUMMARY_HTTP_4XX
    if any(marker in lowered for marker in _DATABASE_MARKERS):
        return True, _ERROR_SUMMARY_DATABASE
    if any(marker in lowered for marker in _VALIDATION_MARKERS):
        return True, _ERROR_SUMMARY_VALIDATION
    return True, _ERROR_SUMMARY_UNKNOWN
