from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_LOADED = False


def load_app_env() -> None:
    """Load .env from the project root without overriding Railway-injected variables."""
    global _ENV_LOADED
    if _ENV_LOADED:
        return

    load_dotenv(_PROJECT_ROOT / ".env", override=False)
    # Fallback for processes whose working directory is not the project root.
    load_dotenv(override=False)
    _ENV_LOADED = True


def get_env(name: str, default: str = "") -> str:
    load_app_env()
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip()


def get_anthropic_api_key() -> str:
    load_app_env()
    for name in ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY"):
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return ""


load_app_env()
