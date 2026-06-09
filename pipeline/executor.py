from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pipeline.lock import is_pipeline_running, read_lock_pid

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_RUN_SCRIPT = _PROJECT_ROOT / "run_pipeline.py"


def start_pipeline_subprocess() -> dict[str, str | int]:
    """Run the pipeline in a detached subprocess so the web server stays responsive."""
    if is_pipeline_running():
        pid = read_lock_pid()
        return {"status": "already_running", "pid": pid or 0}

    process = subprocess.Popen(
        [sys.executable, str(_RUN_SCRIPT)],
        cwd=_PROJECT_ROOT,
        start_new_session=True,
        env=None,
    )
    return {"status": "started", "pid": process.pid}


def pipeline_status() -> dict[str, str | int | bool]:
    pid = read_lock_pid()
    return {
        "running": pid is not None,
        "pid": pid or 0,
    }
