from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict, dataclass, field
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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _load_state() -> RunState | None:
    if not _STATE_PATH.exists():
        return None
    raw = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    return RunState(**raw)


def _run_state_dir() -> Path:
    return _STATE_PATH.parent / f"{_STATE_PATH.stem}_runs"


def _run_state_path(run_id: str) -> Path:
    digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:12]
    safe_prefix = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in run_id)
    safe_prefix = safe_prefix[:48] or "run"
    return _run_state_dir() / f"{safe_prefix}-{digest}.json"


def _load_state_for_run(run_id: str) -> RunState | None:
    active = _load_state()
    if active is not None and active.run_id == run_id:
        return active
    path = _run_state_path(run_id)
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return RunState(**raw)


def _save_active_state(state: RunState) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")


def _save_run_state(state: RunState) -> None:
    path = _run_state_path(state.run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")


def _save_state(state: RunState) -> None:
    _save_run_state(state)
    _save_active_state(state)


def _save_existing_run_state(state: RunState) -> None:
    _save_run_state(state)
    active = _load_state()
    if active is None or active.run_id == state.run_id:
        _save_active_state(state)


def get_run_state(run_id: str | None = None) -> RunState | None:
    with _LOCK:
        if run_id is not None:
            return _load_state_for_run(run_id)
        return _load_state()


def begin_run(run_id: str) -> RunState:
    with _LOCK:
        state = RunState(run_id=run_id, phase="running")
        _save_state(state)
        return state


def begin_tender_scrape(run_id: str) -> None:
    with _LOCK:
        state = _load_state_for_run(run_id)
        if state is None:
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
        state = _load_state_for_run(run_id)
        if state is None:
            raise PipelineOrderError(f"No active run for run_id={run_id}")
        if step not in state.completed_tender_scrapes:
            state.completed_tender_scrapes.append(step)
        missing = [s for s in TENDER_SCRAPE_STEPS if s not in state.completed_tender_scrapes]
        if not missing and not state.tender_scrape_finished_at:
            state.tender_scrape_finished_at = _iso(_utc_now())
            state.phase = "tender_scrape_complete"
        _save_existing_run_state(state)


def complete_tender_scrape(run_id: str) -> None:
    with _LOCK:
        state = _load_state_for_run(run_id)
        if state is None:
            raise PipelineOrderError(f"No active run for run_id={run_id}")
        missing = [s for s in TENDER_SCRAPE_STEPS if s not in state.completed_tender_scrapes]
        if missing:
            raise PipelineOrderError(
                f"Tender scrape incomplete for run_id={run_id}; missing steps: {', '.join(missing)}"
            )
        state.tender_scrape_finished_at = _iso(_utc_now())
        state.phase = "tender_scrape_complete"
        _save_existing_run_state(state)


def begin_full_scrape(run_id: str) -> None:
    with _LOCK:
        state = _load_state_for_run(run_id)
        if state is None:
            state = RunState(run_id=run_id, phase="full_scrape")
        state.scrape_phase_started_at = state.scrape_phase_started_at or _iso(_utc_now())
        state.phase = "full_scrape"
        _save_state(state)


def complete_full_scrape(run_id: str) -> None:
    with _LOCK:
        state = _load_state_for_run(run_id)
        if state is None:
            raise PipelineOrderError(f"No active run for run_id={run_id}")
        state.scrape_phase_finished_at = _iso(_utc_now())
        if state.phase != "tender_scrape_complete":
            state.phase = "scrape_complete"
        _save_existing_run_state(state)


def assert_ready_for_import(run_id: str | None, *, force: bool = False) -> None:
    if force:
        return
    with _LOCK:
        state = _load_state_for_run(run_id) if run_id else _load_state()
        if state is None:
            raise PipelineOrderError(
                "Import blocked: no pipeline run has completed tender scrapers. "
                "Run the full scrape phase first."
            )
        missing = [s for s in TENDER_SCRAPE_STEPS if s not in state.completed_tender_scrapes]
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
        state = _load_state_for_run(run_id)
        if state is None:
            raise PipelineOrderError(f"No active run for run_id={run_id}")
        state.phase = "import"
        state.import_started_at = _iso(_utc_now())
        _save_existing_run_state(state)


def complete_import(run_id: str) -> None:
    with _LOCK:
        state = _load_state_for_run(run_id)
        if state is None:
            raise PipelineOrderError(f"No active run for run_id={run_id}")
        state.import_finished_at = _iso(_utc_now())
        state.phase = "import_complete"
        _save_existing_run_state(state)


def finish_run(run_id: str, *, success: bool, error: str = "") -> None:
    with _LOCK:
        state = _load_state_for_run(run_id)
        if state is None:
            return
        state.phase = "finished"
        state.finished_at = _iso(_utc_now())
        state.success = success
        state.error = error[:4000]
        _save_existing_run_state(state)


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
