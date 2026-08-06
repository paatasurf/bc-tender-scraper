"""Pipeline run coordinator -- cutover dispatcher (R1).

Selects between two backend implementations via the
PIPELINE_COORDINATOR_BACKEND environment variable:

  - "legacy"   (default): pipeline/run_coordinator_legacy.py -- the
               original local .pipeline/run_coordinator.json +
               threading.Lock() implementation, byte-for-byte unchanged
               behavior. Never touches the database.
  - "postgres": pipeline/run_coordinator_postgres.py -- the new
               PostgreSQL-backed implementation (migration 032). Requires
               that migration to have already been applied via
               scripts/run_pipeline_coordinator_state_migration.py
               --apply; if it hasn't, every call raises
               PipelineCoordinatorSchemaNotReadyError immediately (before
               any lock or mutation) rather than hitting a raw Postgres
               error or silently doing nothing.

Any other value is a fail-closed startup/first-use error
(PipelineCoordinatorBackendError) -- never silently defaults to a working
backend, and postgres backend errors (including a missing schema) never
fall back to legacy. The backend is re-resolved on every call (cheap: one
env lookup) rather than cached, so this stays correct even if a test or a
future caller changes the environment mid-process; production code never
does that.

Why this dispatcher exists: this PR (R1) ships new code -- Railway will
deploy it before anyone has run the Class D migration apply step. Without
this flag, pipeline/run_coordinator.py would try to read/write tables that
don't exist yet on first deploy and break the scheduled pipeline. With
PIPELINE_COORDINATOR_BACKEND defaulting to "legacy", this PR is a no-op in
production until an operator deliberately flips the flag -- see the
rollout runbook in scripts/run_pipeline_coordinator_state_migration.py's
module docstring.

The public API below is unchanged from the pre-R1 (JSON-only) module --
every existing caller (pipeline/tender_data_pipeline.py,
pipeline/internal_steps.py, scripts/audit_pipeline_ordering.py) keeps
working without modification, on either backend.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from config.env import get_env
from pipeline.run_coordinator_types import (
    TENDER_SCRAPE_STEPS,
    PipelineOrderError,
    PipelineRunConflictError,
    RunState,
)

if TYPE_CHECKING:
    from types import ModuleType

__all__ = [
    "TENDER_SCRAPE_STEPS",
    "PipelineOrderError",
    "PipelineRunConflictError",
    "PipelineCoordinatorBackendError",
    "PipelineCoordinatorSchemaNotReadyError",
    "RunState",
    "begin_run",
    "begin_or_resume_run",
    "begin_tender_scrape",
    "mark_tender_scrape_step",
    "complete_tender_scrape",
    "begin_full_scrape",
    "complete_full_scrape",
    "assert_ready_for_import",
    "begin_import",
    "complete_import",
    "finish_run",
    "get_run_state",
    "assert_import_not_before_scrape",
]

_BACKEND_ENV_VAR = "PIPELINE_COORDINATOR_BACKEND"
_DEFAULT_BACKEND = "legacy"
_VALID_BACKENDS = ("legacy", "postgres")


class PipelineCoordinatorBackendError(RuntimeError):
    """Raised when PIPELINE_COORDINATOR_BACKEND is set to anything other
    than "legacy" or "postgres". Fail-closed: checked on every call,
    never silently falls through to a working backend."""


# Re-exported so `except PipelineCoordinatorSchemaNotReadyError` works from
# this module without callers needing to know which backend module defines
# it; only meaningful for the postgres backend.
from pipeline.run_coordinator_postgres import (  # noqa: E402
    PipelineCoordinatorSchemaNotReadyError,
)


def _resolve_backend() -> str:
    raw = get_env(_BACKEND_ENV_VAR, _DEFAULT_BACKEND).strip().lower()
    if raw not in _VALID_BACKENDS:
        raise PipelineCoordinatorBackendError(
            f"Invalid {_BACKEND_ENV_VAR}={raw!r}. Must be one of {list(_VALID_BACKENDS)!r}."
        )
    return raw


def _backend_module() -> "ModuleType":
    if _resolve_backend() == "postgres":
        from pipeline import run_coordinator_postgres as module
    else:
        from pipeline import run_coordinator_legacy as module
    return module


def begin_run(run_id: str) -> RunState:
    return _backend_module().begin_run(run_id)


def begin_or_resume_run(run_id: str | None = None) -> RunState:
    return _backend_module().begin_or_resume_run(run_id)


def begin_tender_scrape(run_id: str) -> None:
    _backend_module().begin_tender_scrape(run_id)


def mark_tender_scrape_step(run_id: str, step: str) -> None:
    _backend_module().mark_tender_scrape_step(run_id, step)


def complete_tender_scrape(run_id: str) -> None:
    _backend_module().complete_tender_scrape(run_id)


def begin_full_scrape(run_id: str) -> None:
    _backend_module().begin_full_scrape(run_id)


def complete_full_scrape(run_id: str) -> None:
    _backend_module().complete_full_scrape(run_id)


def assert_ready_for_import(run_id: str | None, *, force: bool = False) -> None:
    _backend_module().assert_ready_for_import(run_id, force=force)


def begin_import(run_id: str) -> None:
    _backend_module().begin_import(run_id)


def complete_import(run_id: str) -> None:
    _backend_module().complete_import(run_id)


def finish_run(run_id: str, *, success: bool, error: str = "") -> None:
    _backend_module().finish_run(run_id, success=success, error=error)


def get_run_state() -> RunState | None:
    return _backend_module().get_run_state()


def assert_import_not_before_scrape() -> dict[str, str | None]:
    return _backend_module().assert_import_not_before_scrape()
