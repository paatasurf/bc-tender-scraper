from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_STATE_PATH = _PROJECT_ROOT / ".pipeline" / "run_coordinator.json"
_LOCK = threading.Lock()

TENDER_SCRAPE_STEPS: tuple[str, ...] = (
    "scrape-federal",
    "scrape-merx-arch",
    "scrape-commercial",
)


class PipelineOrderError(RuntimeError):
    """Raised when a pipeline step runs out of order."""


@dataclass
class RunState:
    run_id: str
    phase: str = "idle"
    tender_scrape_started_at: str | None = None
    tender_scrape_finished_at: str | None = None
    import_started_at: str | None = None
    import_finished_at: str | None = None
    completed_tender_scrapes: list[str] = field(default_factory=list)
    scrape_phase_started_at: str | None = None
    scrape_phase_finished_at: str | None = None
    finished_at: str | None = None
    success: bool | None = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_RUN_STATE_FIELDS = {field.name for field in fields(RunState)}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _coerce_run_state(raw: dict[str, Any]) -> RunState:
    return RunState(
        **{key: value for key, value in raw.items() if key in _RUN_STATE_FIELDS}
    )


def _load_states() -> tuple[dict[str, RunState], str | None]:
    if not _STATE_PATH.exists():
        return {}, None
    raw = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("runs"), dict):
        states = {
            run_id: _coerce_run_state(state)
            for run_id, state in raw["runs"].items()
            if isinstance(state, dict)
        }
        active_run_id = raw.get("active_run_id")
        if active_run_id not in states:
            active_run_id = next(iter(states), None)
        return states, active_run_id

    state = _coerce_run_state(raw)
    return {state.run_id: state}, state.run_id


def _load_state() -> RunState | None:
    states, active_run_id = _load_states()
    if active_run_id is None:
        return None
    return states.get(active_run_id)


def _save_state(state: RunState) -> None:
    _save_states({state.run_id: state}, state.run_id)


def _save_states(states: dict[str, RunState], active_run_id: str | None) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if len(states) == 1 and active_run_id in states:
        payload: dict[str, Any] = states[active_run_id].to_dict()
    else:
        payload = {
            "active_run_id": active_run_id,
            "runs": {run_id: state.to_dict() for run_id, state in states.items()},
        }
    _STATE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def get_run_state() -> RunState | None:
    with _LOCK:
        return _load_state()


def begin_run(run_id: str) -> RunState:
    with _LOCK:
        states, _active_run_id = _load_states()
        state = RunState(run_id=run_id, phase="running")
        states[run_id] = state
        _save_states(states, run_id)
        return state


def begin_tender_scrape(run_id: str) -> None:
    with _LOCK:
        states, _active_run_id = _load_states()
        state = states.get(run_id)
        if state is None:
            state = RunState(run_id=run_id, phase="tender_scrape")
        now = _iso(_utc_now())
        state.phase = "tender_scrape"
        state.tender_scrape_started_at = state.tender_scrape_started_at or now
        state.scrape_phase_started_at = state.scrape_phase_started_at or now
        states[run_id] = state
        _save_states(states, run_id)


def mark_tender_scrape_step(run_id: str, step: str) -> None:
    if step not in TENDER_SCRAPE_STEPS:
        raise ValueError(f"Unknown tender scrape step: {step}")
    with _LOCK:
        states, active_run_id = _load_states()
        state = states.get(run_id)
        if state is None:
            raise PipelineOrderError(f"No active run for run_id={run_id}")
        if step not in state.completed_tender_scrapes:
            state.completed_tender_scrapes.append(step)
        missing = [
            s for s in TENDER_SCRAPE_STEPS if s not in state.completed_tender_scrapes
        ]
        if not missing and not state.tender_scrape_finished_at:
            state.tender_scrape_finished_at = _iso(_utc_now())
            state.phase = "tender_scrape_complete"
        states[run_id] = state
        _save_states(states, active_run_id)


def complete_tender_scrape(run_id: str) -> None:
    with _LOCK:
        states, active_run_id = _load_states()
        state = states.get(run_id)
        if state is None:
            raise PipelineOrderError(f"No active run for run_id={run_id}")
        missing = [s for s in TENDER_SCRAPE_STEPS if s not in state.completed_tender_scrapes]
        if missing:
            raise PipelineOrderError(
                f"Tender scrape incomplete for run_id={run_id}; missing steps: {', '.join(missing)}"
            )
        state.tender_scrape_finished_at = _iso(_utc_now())
        state.phase = "tender_scrape_complete"
        states[run_id] = state
        _save_states(states, active_run_id)


def begin_full_scrape(run_id: str) -> None:
    with _LOCK:
        states, _active_run_id = _load_states()
        state = states.get(run_id)
        if state is None:
            state = RunState(run_id=run_id, phase="full_scrape")
        state.scrape_phase_started_at = state.scrape_phase_started_at or _iso(
            _utc_now()
        )
        state.phase = "full_scrape"
        states[run_id] = state
        _save_states(states, run_id)


def complete_full_scrape(run_id: str) -> None:
    with _LOCK:
        states, active_run_id = _load_states()
        state = states.get(run_id)
        if state is None:
            raise PipelineOrderError(f"No active run for run_id={run_id}")
        state.scrape_phase_finished_at = _iso(_utc_now())
        if state.phase != "tender_scrape_complete":
            state.phase = "scrape_complete"
        states[run_id] = state
        _save_states(states, active_run_id)


def assert_ready_for_import(run_id: str | None, *, force: bool = False) -> None:
    if force:
        return
    with _LOCK:
        states, active_run_id = _load_states()
        state = states.get(run_id) if run_id else states.get(active_run_id)
        if state is None:
            raise PipelineOrderError(
                "Import blocked: no pipeline run has completed tender scrapers. "
                "Run the full scrape phase first."
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
        states, active_run_id = _load_states()
        state = states.get(run_id)
        if state is None:
            raise PipelineOrderError(f"No active run for run_id={run_id}")
        state.phase = "import"
        state.import_started_at = _iso(_utc_now())
        states[run_id] = state
        _save_states(states, active_run_id)


def complete_import(run_id: str) -> None:
    with _LOCK:
        states, active_run_id = _load_states()
        state = states.get(run_id)
        if state is None:
            raise PipelineOrderError(f"No active run for run_id={run_id}")
        state.import_finished_at = _iso(_utc_now())
        state.phase = "import_complete"
        states[run_id] = state
        _save_states(states, active_run_id)


def finish_run(run_id: str, *, success: bool, error: str = "") -> None:
    with _LOCK:
        states, active_run_id = _load_states()
        state = states.get(run_id)
        if state is None:
            return
        state.phase = "finished"
        state.finished_at = _iso(_utc_now())
        state.success = success
        state.error = error[:4000]
        states[run_id] = state
        _save_states(states, active_run_id)


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
