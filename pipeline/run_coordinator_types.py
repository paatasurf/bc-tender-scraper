"""Shared types for pipeline/run_coordinator.py's backend cutover
(PIPELINE_COORDINATOR_BACKEND=legacy|postgres).

Defined once here -- not in either backend module -- so that
`except PipelineOrderError` (and RunState's shape) is identical regardless
of which backend actually raised/returned it. pipeline/run_coordinator.py
re-exports all of these; callers should keep importing from there.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

TENDER_SCRAPE_STEPS: tuple[str, ...] = (
    "scrape-federal",
    "scrape-merx-arch",
    "scrape-commercial",
)


class PipelineOrderError(RuntimeError):
    """Raised when a pipeline step runs out of order, or a run/step
    reference is invalid."""


class PipelineRunConflictError(PipelineOrderError):
    """Raised when a different explicit run_id is requested while another
    run is already active for the same scope (postgres backend only --
    the legacy backend has no such concept). A PipelineOrderError
    subclass, so existing ``except PipelineOrderError`` callers keep
    working unchanged."""


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
        return {
            "run_id": self.run_id,
            "phase": self.phase,
            "tender_scrape_started_at": self.tender_scrape_started_at,
            "tender_scrape_finished_at": self.tender_scrape_finished_at,
            "import_started_at": self.import_started_at,
            "import_finished_at": self.import_finished_at,
            "completed_tender_scrapes": list(self.completed_tender_scrapes),
            "scrape_phase_started_at": self.scrape_phase_started_at,
            "scrape_phase_finished_at": self.scrape_phase_finished_at,
            "finished_at": self.finished_at,
            "success": self.success,
            "error": self.error,
        }
