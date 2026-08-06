"""Legacy pipeline run coordinator backend -- local JSON file +
threading.Lock(), unchanged from the pre-R1 implementation.

Selected by PIPELINE_COORDINATOR_BACKEND=legacy (the default -- see
pipeline/run_coordinator.py). Every function here has byte-for-byte the
same behavior it always had; only the RunState/PipelineOrderError/
TENDER_SCRAPE_STEPS imports moved to pipeline.run_coordinator_types so
both backends share one set of types (see that module's docstring for
why). begin_or_resume_run() is new (needed for API parity with the
postgres backend, which callers may switch to via the flag) but is purely
additive and does not change any existing function's behavior -- it is
just begin_run() with an optional run_id, which is exactly what
tender_data_pipeline.py's ``run_id or new_run_id()`` pattern already does
at call sites today.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from pipeline.run_coordinator_types import (
    TENDER_SCRAPE_STEPS,
    PipelineOrderError,
    RunState,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_STATE_PATH = _PROJECT_ROOT / ".pipeline" / "run_coordinator.json"
_LOCK = threading.Lock()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _load_state() -> RunState | None:
    if not _STATE_PATH.exists():
        return None
    raw = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    return RunState(**raw)


def _save_state(state: RunState) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")


def get_run_state() -> RunState | None:
    with _LOCK:
        return _load_state()


def begin_run(run_id: str) -> RunState:
    with _LOCK:
        state = RunState(run_id=run_id, phase="running")
        _save_state(state)
        return state


def begin_or_resume_run(run_id: str | None = None) -> RunState:
    """API parity with the postgres backend. The legacy backend has never
    had resume/conflict semantics -- this is exactly begin_run() with an
    optional run_id, matching what callers already do today
    (``run_id or new_run_id()``)."""
    from pipeline.runs import new_run_id

    return begin_run(run_id or new_run_id())


def begin_tender_scrape(run_id: str) -> None:
    with _LOCK:
        state = _load_state()
        if state is None or state.run_id != run_id:
            state = RunState(run_id=run_id, phase="tender_scrape")
        now = _iso(_utc_now())
        state.phase = "tender_scrape"
        state.tender_scrape_started_at = state.tender_scrape_started_at or now
        state.scrape_phase_started_at = state.scrape_phase_started_at or now
        _save_state(state)


def mark_tender_scrape_step(run_id: str, step: str) -> None:
    if step not in TENDER_SCRAPE_STEPS:
        raise ValueError(f"Unknown tender scrape step: {step}")
    with _LOCK:
        state = _load_state()
        if state is None or state.run_id != run_id:
            raise PipelineOrderError(f"No active run for run_id={run_id}")
        if step not in state.completed_tender_scrapes:
            state.completed_tender_scrapes.append(step)
        missing = [
            s for s in TENDER_SCRAPE_STEPS if s not in state.completed_tender_scrapes
        ]
        if not missing and not state.tender_scrape_finished_at:
            state.tender_scrape_finished_at = _iso(_utc_now())
            state.phase = "tender_scrape_complete"
        _save_state(state)


def complete_tender_scrape(run_id: str) -> None:
    with _LOCK:
        state = _load_state()
        if state is None or state.run_id != run_id:
            raise PipelineOrderError(f"No active run for run_id={run_id}")
        missing = [
            s for s in TENDER_SCRAPE_STEPS if s not in state.completed_tender_scrapes
        ]
        if missing:
            raise PipelineOrderError(
                f"Tender scrape incomplete for run_id={run_id}; missing steps: {', '.join(missing)}"
            )
        state.tender_scrape_finished_at = _iso(_utc_now())
        state.phase = "tender_scrape_complete"
        _save_state(state)


def begin_full_scrape(run_id: str) -> None:
    with _LOCK:
        state = _load_state()
        if state is None or state.run_id != run_id:
            state = RunState(run_id=run_id, phase="full_scrape")
        state.scrape_phase_started_at = state.scrape_phase_started_at or _iso(
            _utc_now()
        )
        state.phase = "full_scrape"
        _save_state(state)


def complete_full_scrape(run_id: str) -> None:
    with _LOCK:
        state = _load_state()
        if state is None or state.run_id != run_id:
            raise PipelineOrderError(f"No active run for run_id={run_id}")
        state.scrape_phase_finished_at = _iso(_utc_now())
        if state.phase != "tender_scrape_complete":
            state.phase = "scrape_complete"
        _save_state(state)


def assert_ready_for_import(run_id: str | None, *, force: bool = False) -> None:
    if force:
        return
    with _LOCK:
        state = _load_state()
        if state is None:
            raise PipelineOrderError(
                "Import blocked: no pipeline run has completed tender scrapers. "
                "Run the full scrape phase first."
            )
        if run_id and state.run_id != run_id:
            raise PipelineOrderError(
                f"Import blocked: active run is {state.run_id!r}, requested {run_id!r}"
            )
        missing = [
            s for s in TENDER_SCRAPE_STEPS if s not in state.completed_tender_scrapes
        ]
        if missing:
            raise PipelineOrderError(
                "Import blocked: tender scrapers have not finished. "
                f"Missing steps: {', '.join(missing)}"
            )
        if not state.tender_scrape_finished_at:
            raise PipelineOrderError(
                "Import blocked: tender scrape phase has not been marked complete."
            )


def begin_import(run_id: str) -> None:
    with _LOCK:
        state = _load_state()
        if state is None or state.run_id != run_id:
            raise PipelineOrderError(f"No active run for run_id={run_id}")
        state.phase = "import"
        state.import_started_at = _iso(_utc_now())
        _save_state(state)


def complete_import(run_id: str) -> None:
    with _LOCK:
        state = _load_state()
        if state is None or state.run_id != run_id:
            raise PipelineOrderError(f"No active run for run_id={run_id}")
        state.import_finished_at = _iso(_utc_now())
        state.phase = "import_complete"
        _save_state(state)


def finish_run(run_id: str, *, success: bool, error: str = "") -> None:
    with _LOCK:
        state = _load_state()
        if state is None or state.run_id != run_id:
            return
        state.phase = "finished"
        state.finished_at = _iso(_utc_now())
        state.success = success
        state.error = error[:4000]
        _save_state(state)


def assert_import_not_before_scrape() -> dict[str, str | None]:
    """Return ordering audit fields for logging/tests."""
    state = get_run_state()
    if state is None:
        return {
            "tender_scrape_started_at": None,
            "tender_scrape_finished_at": None,
            "import_started_at": None,
            "import_finished_at": None,
            "ordering_ok": "unknown",
        }
    ordering_ok = "unknown"
    if state.import_started_at and state.tender_scrape_finished_at:
        ordering_ok = str(state.import_started_at >= state.tender_scrape_finished_at)
    return {
        "tender_scrape_started_at": state.tender_scrape_started_at,
        "tender_scrape_finished_at": state.tender_scrape_finished_at,
        "import_started_at": state.import_started_at,
        "import_finished_at": state.import_finished_at,
        "ordering_ok": ordering_ok,
    }
