from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_LOADED = False


def load_app_env() -> None:
    """Load env files without overriding explicit process environment variables.

    Load order:
      1. Snapshot keys already in ``os.environ`` (Railway / shell / test harness)
      2. ``.env`` — shared secrets; production URL belongs in DATABASE_URL_PRODUCTION
      3. ``.env.local`` — overrides ``.env`` for keys not in the startup snapshot
      4. CWD ``.env`` fallback
    """
    global _ENV_LOADED
    if _ENV_LOADED:
        return

    preserved_keys = set(os.environ.keys())

    load_dotenv(_PROJECT_ROOT / ".env", override=False, encoding="utf-8-sig")

    local_path = _PROJECT_ROOT / ".env.local"
    if local_path.is_file():
        for key, value in dotenv_values(local_path, encoding="utf-8-sig").items():
            if value is None or key in preserved_keys:
                continue
            os.environ[key] = value.strip()

    load_dotenv(override=False, encoding="utf-8-sig")
    _ENV_LOADED = True


def get_env(name: str, default: str = "") -> str:
    load_app_env()
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip()


def env_flag(name: str, *, default: bool = False) -> bool:
    load_app_env()
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes"}


def get_anthropic_api_key() -> str:
    load_app_env()
    for name in ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY"):
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return ""


load_app_env()
