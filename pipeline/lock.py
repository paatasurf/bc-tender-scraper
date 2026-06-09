from __future__ import annotations

import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOCK_PATH = _PROJECT_ROOT / ".pipeline.lock"


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def read_lock_pid() -> int | None:
    if not LOCK_PATH.exists():
        return None
    try:
        pid = int(LOCK_PATH.read_text(encoding="utf-8").strip())
    except ValueError:
        return None
    if not _pid_is_running(pid):
        LOCK_PATH.unlink(missing_ok=True)
        return None
    return pid


def is_pipeline_running() -> bool:
    return read_lock_pid() is not None


def acquire_lock() -> None:
    existing = read_lock_pid()
    if existing is not None:
        raise RuntimeError(f"Pipeline already running (pid {existing})")

    LOCK_PATH.write_text(str(os.getpid()), encoding="utf-8")


def release_lock() -> None:
    LOCK_PATH.unlink(missing_ok=True)
