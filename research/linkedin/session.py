"""Authentication helpers — persistent browser profile (default) + storageState fallback."""

from __future__ import annotations

import os
from pathlib import Path

from research.linkedin.paths import (
    AUTH_DIR,
    BROWSER_PROFILE_DIR,
    DEFAULT_SESSION_PATH,
    SESSION_DIR,
    SESSION_ENV,
)

LOGIN_PATH_MARKERS = ("/login", "/checkpoint/", "/authwall", "/uas/login")
REPO_ROOT_DISPLAY = Path(__file__).resolve().parents[2]


class SessionExpiredError(RuntimeError):
    """Raised when storageState session JSON is missing, invalid, or expired."""


class ProfileExpiredError(RuntimeError):
    """Raised when persistent browser profile is missing or logged out."""


PROFILE_REFRESH_STEPS = f"""LinkedIn browser profile expired or not set up.

Refresh manually (under 2 minutes):

  cd {REPO_ROOT_DISPLAY}
  python research/linkedin/scripts/login_profile.py

Log in in the browser window (MFA OK). Press Enter when your feed loads.
Then re-run your batch command."""


SESSION_REFRESH_STEPS = f"""LinkedIn storageState session expired or not found (fallback mode).

Refresh manually (under 2 minutes):

  cd {REPO_ROOT_DISPLAY}
  python research/linkedin/scripts/create_session.py

Then set (optional):

  $env:LINKEDIN_SESSION_PATH = "{DEFAULT_SESSION_PATH}"

Re-run your pipeline command."""


def is_login_url(url: str) -> bool:
    lower = (url or "").lower()
    return any(marker in lower for marker in LOGIN_PATH_MARKERS)


def profile_is_initialized() -> bool:
    if not BROWSER_PROFILE_DIR.exists():
        return False
    markers = ("Default", "Local State", "Cookies")
    return any((BROWSER_PROFILE_DIR / name).exists() for name in markers)


def resolve_session_path(explicit: str | Path | None = None) -> str | None:
    if explicit:
        return str(explicit)
    env_path = os.environ.get(SESSION_ENV, "").strip()
    if env_path:
        return env_path
    if DEFAULT_SESSION_PATH.is_file():
        return str(DEFAULT_SESSION_PATH)
    return None


def require_session_path(explicit: str | Path | None = None) -> str:
    path = resolve_session_path(explicit)
    if not path or not Path(path).is_file():
        raise SessionExpiredError(f"No session JSON found.\n\n{SESSION_REFRESH_STEPS}")
    return path


def resolve_auth_mode(*, prefer_profile: bool = True, force_session: bool = False) -> str:
    """Return 'profile', 'session', or 'public'."""
    if force_session:
        return "session" if resolve_session_path() else "public"
    if prefer_profile and profile_is_initialized():
        return "profile"
    if resolve_session_path():
        return "session"
    return "public"


def print_profile_refresh_message() -> None:
    print(PROFILE_REFRESH_STEPS, flush=True)


def print_session_refresh_message() -> None:
    print(SESSION_REFRESH_STEPS, flush=True)
