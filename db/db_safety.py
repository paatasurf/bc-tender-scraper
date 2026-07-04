"""Production database guard rails for CLI scripts.

Classification (see scripts/CLASSIFICATION.md):
  Class A — no write (no DB, or read-only SELECT)
  Class B — local data write (backfill, cache, staging)
  Class C — registry write (dry-run + apply)
  Class D — schema DDL (init_db, migrations)

Runtime escalation: the highest-risk operation actually executed determines
the EFFECTIVE class. init_db() / DDL always escalates to Class D mid-run.
"""

from __future__ import annotations

import getpass
import io
import os
import sys
import threading
from argparse import ArgumentParser, Namespace
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from sqlalchemy.engine import make_url

from config.env import get_env, load_app_env
from db.classification import SafetyClass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DESTRUCTIVE_LOG_PATH = PROJECT_ROOT / "logs" / "destructive_operations.log"

PRODUCTION_CONFIRMATION = "I UNDERSTAND THIS WILL MODIFY PRODUCTION"

DbMode = Literal["READ-ONLY", "DESTRUCTIVE"]

_PRODUCTION_HOST_MARKERS = (
    ".proxy.rlwy.net",
    ".rlwy.net",
    ".railway.internal",
)

_guard_local = threading.local()


@dataclass
class _GuardContext:
    script_name: str
    nominal_class: SafetyClass
    effective_class: SafetyClass
    allow_production: bool
    class_d_authorized: bool = False


def _get_context() -> _GuardContext | None:
    return getattr(_guard_local, "ctx", None)


def _set_context(ctx: _GuardContext) -> None:
    _guard_local.ctx = ctx


def effective_class() -> SafetyClass | None:
    ctx = _get_context()
    return ctx.effective_class if ctx else None


def is_production_host(host: str) -> bool:
    normalized = (host or "").strip().lower()
    if not normalized:
        return False
    extra = get_env("DB_PRODUCTION_HOSTS", "")
    if extra:
        for item in extra.split(","):
            item = item.strip().lower()
            if item and (normalized == item or normalized.endswith("." + item)):
                return True
    return any(marker in normalized for marker in _PRODUCTION_HOST_MARKERS)


def is_production_database_url(url: str) -> bool:
    if not url or not url.strip():
        return False
    try:
        host = (make_url(url).host or "").lower()
    except Exception:
        return False
    return is_production_host(host)


def _parse_url_parts(url: str) -> tuple[str, str]:
    try:
        parsed = make_url(url)
        return parsed.host or "unknown", parsed.database or "unknown"
    except Exception:
        return "unknown", "unknown"


def resolve_script_database_url(*, use_production: bool = False) -> str:
    load_app_env()
    if use_production:
        prod = get_env("DATABASE_URL_PRODUCTION")
        if not prod:
            raise RuntimeError(
                "DATABASE_URL_PRODUCTION is not set. "
                "Store the Railway URL there — never in DATABASE_URL."
            )
        return prod
    url = get_env("DATABASE_URL")
    if url:
        return url
    raise RuntimeError(
        "DATABASE_URL is not set. Copy .env.local.example to .env.local "
        "and point DATABASE_URL at local Postgres."
    )


def apply_script_database_url(*, use_production: bool = False) -> str:
    from db.connection import clear_engine_cache

    url = resolve_script_database_url(use_production=use_production)
    os.environ["DATABASE_URL"] = url
    clear_engine_cache()
    return url


def print_database_banner(
    *,
    script_name: str,
    url: str,
    mode: DbMode,
    nominal_class: SafetyClass | None = None,
    effective_class_label: SafetyClass | None = None,
) -> None:
    host, database = _parse_url_parts(url)
    environment = "PRODUCTION" if is_production_database_url(url) else "LOCAL"
    print("")
    print("=" * 57)
    print("Target Database")
    print(f"Environment: {environment}")
    print(f"Host: {host}")
    print(f"Database: {database}")
    print(f"Mode: {mode}")
    if nominal_class is not None:
        print(f"Nominal Class: {nominal_class.label}")
    if effective_class_label is not None:
        print(f"Effective Class: {effective_class_label.label}")
    print(f"Script: {script_name}")
    print("=" * 57)
    print("")


def add_production_safety_args(parser: ArgumentParser) -> None:
    parser.add_argument(
        "--allow-production",
        action="store_true",
        help=(
            "Allow Class C/D writes against DATABASE_URL_PRODUCTION "
            f"(requires typing: {PRODUCTION_CONFIRMATION!r})"
        ),
    )
    parser.add_argument(
        "--use-production",
        action="store_true",
        help="Class B read-only via DATABASE_URL_PRODUCTION (banner only)",
    )


def _log_destructive_operation(*, script_name: str, url: str, user: str) -> None:
    DESTRUCTIVE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    host, database = _parse_url_parts(url)
    timestamp = datetime.now(timezone.utc).isoformat()
    line = (
        f"{timestamp}\tscript={script_name}\thost={host}\tdatabase={database}\t"
        f"user={user}\tconfirmed=true\n"
    )
    with DESTRUCTIVE_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line)


def _stdin_is_interactive_tty() -> bool:
    """True only when stdin is the process interactive TTY (not pipe/mock/redirect)."""
    if sys.stdin is not sys.__stdin__:
        return False
    try:
        fd = sys.stdin.fileno()
    except (AttributeError, OSError, ValueError, io.UnsupportedOperation):
        return False
    if not os.isatty(fd):
        return False
    # Block unittest.mock patches on sys.stdin.isatty() when fd is not a TTY.
    if hasattr(sys.stdin, "isatty") and not sys.stdin.isatty():
        return False
    return True


def _input_is_unmocked_builtin() -> bool:
    """True when builtins.input is the real interpreter builtin (not unittest.mock)."""
    import builtins

    fn = builtins.input
    mod = getattr(type(fn), "__module__", "") or getattr(fn, "__module__", "")
    if "mock" in mod.lower():
        return False
    return getattr(fn, "__name__", "") == "input" and getattr(fn, "__module__", "") in (
        "builtins",
        "_io",
    )


def _require_interactive_production_confirmation() -> bool:
    if not _stdin_is_interactive_tty():
        print(
            "[db_safety] Refusing production write: interactive confirmation "
            "requires a real terminal (TTY). Piped, redirected, or mocked stdin "
            "is not accepted.\n"
            "  Run --apply in your own terminal and type the confirmation phrase "
            "manually. Agents and CI cannot satisfy this gate.",
            file=sys.stderr,
        )
        return False
    if not _input_is_unmocked_builtin():
        print(
            "[db_safety] Refusing production write: mocked or patched input() "
            "is not accepted for Class C/D production confirmation.\n"
            "  Run --apply in your own terminal and type the confirmation phrase "
            "manually.",
            file=sys.stderr,
        )
        return False
    print("")
    print("!" * 57)
    print("  WARNING: Class D operation on PRODUCTION.")
    print(f"  Type exactly: {PRODUCTION_CONFIRMATION}")
    print("!" * 57)
    try:
        typed = input("> ")
    except EOFError:
        return False
    if typed.strip() != PRODUCTION_CONFIRMATION:
        print("[db_safety] Confirmation phrase did not match. Aborting.", file=sys.stderr)
        return False
    return True


def _authorize_class_d(
    *,
    script_name: str,
    allow_production: bool,
    operation: str,
    nominal_class: SafetyClass,
    escalated: bool,
) -> str:
    load_app_env()
    local_url = resolve_script_database_url(use_production=False)

    if is_production_database_url(local_url) and not allow_production:
        print_database_banner(
            script_name=script_name,
            url=local_url,
            mode="DESTRUCTIVE",
            nominal_class=nominal_class,
            effective_class_label=SafetyClass.D,
        )
        print(
            "[db_safety] Refusing to execute.\n"
            f"  Operation: {operation}\n"
            "  DATABASE_URL must point at local Postgres (.env.local).\n"
            "  Move the Railway URL to DATABASE_URL_PRODUCTION.\n"
            "  Use --allow-production and type the confirmation phrase to continue.\n"
            f"  Required phrase: {PRODUCTION_CONFIRMATION!r}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    if allow_production:
        url = apply_script_database_url(use_production=True)
        if not is_production_database_url(url):
            host, _ = _parse_url_parts(url)
            print(
                f"[db_safety] REFUSED: --allow-production requires "
                f"DATABASE_URL_PRODUCTION to be a production host (got {host}).",
                file=sys.stderr,
            )
            raise SystemExit(1)
        print_database_banner(
            script_name=script_name,
            url=url,
            mode="DESTRUCTIVE",
            nominal_class=nominal_class,
            effective_class_label=SafetyClass.D,
        )
        if escalated:
            print(
                f"[db_safety] Runtime escalation to Class D for: {operation}",
                file=sys.stderr,
            )
        if not _require_interactive_production_confirmation():
            raise SystemExit(1)
        user = getpass.getuser()
        _log_destructive_operation(script_name=script_name, url=url, user=user)
        print(f"[db_safety] Logged to {DESTRUCTIVE_LOG_PATH}")
        return url

    url = apply_script_database_url(use_production=False)
    print_database_banner(
        script_name=script_name,
        url=url,
        mode="DESTRUCTIVE",
        nominal_class=nominal_class,
        effective_class_label=SafetyClass.D,
    )
    if escalated:
        print(
            f"[db_safety] Runtime escalation to Class D for: {operation}",
            file=sys.stderr,
        )
    print("[db_safety] Proceeding...")
    return url


def begin_script_guard(
    nominal_class: SafetyClass,
    script_name: str,
    *,
    allow_production: bool = False,
    use_production: bool = False,
) -> str:
    """Startup guard using nominal class. May be escalated later at runtime."""
    ctx = _GuardContext(
        script_name=script_name,
        nominal_class=nominal_class,
        effective_class=nominal_class,
        allow_production=allow_production,
    )
    _set_context(ctx)

    if nominal_class == SafetyClass.A:
        url = apply_script_database_url(use_production=use_production)
        print_database_banner(
            script_name=script_name,
            url=url,
            mode="READ-ONLY",
            nominal_class=nominal_class,
            effective_class_label=nominal_class,
        )
        return url

    if nominal_class == SafetyClass.B:
        url = apply_script_database_url(use_production=False)
        print_database_banner(
            script_name=script_name,
            url=url,
            mode="DESTRUCTIVE",
            nominal_class=nominal_class,
            effective_class_label=nominal_class,
        )
        if is_production_database_url(url) and not allow_production:
            print(
                "[db_safety] Refusing Class B local write on production DATABASE_URL.\n"
                "  Use --allow-production and type the confirmation phrase.",
                file=sys.stderr,
            )
            raise SystemExit(1)
        print("[db_safety] Proceeding at Class B (local write; may escalate to D)...")
        return url

    if nominal_class == SafetyClass.C:
        url = apply_script_database_url(use_production=False)
        print_database_banner(
            script_name=script_name,
            url=url,
            mode="DESTRUCTIVE",
            nominal_class=nominal_class,
            effective_class_label=nominal_class,
        )
        if is_production_database_url(url) and not allow_production:
            print(
                "[db_safety] Refusing Class C write on production DATABASE_URL.\n"
                "  Use --allow-production after a fresh dry-run artifact.",
                file=sys.stderr,
            )
            raise SystemExit(1)
        print("[db_safety] Proceeding at nominal Class C (may escalate to D)...")
        return url

    url = _authorize_class_d(
        script_name=script_name,
        allow_production=allow_production,
        operation="startup Class D",
        nominal_class=SafetyClass.D,
        escalated=False,
    )
    ctx.effective_class = SafetyClass.D
    ctx.class_d_authorized = True
    return url


def require_runtime_class_d(operation: str) -> None:
    """Mid-run escalation hook — init_db() and DDL must pass Class D authorization."""
    ctx = _get_context()
    if ctx is None:
        # FastAPI / Railway service startup — not a guarded CLI script.
        return

    if ctx.class_d_authorized:
        return

    ctx.effective_class = SafetyClass.D
    _authorize_class_d(
        script_name=ctx.script_name,
        allow_production=ctx.allow_production,
        operation=operation,
        nominal_class=ctx.nominal_class,
        escalated=ctx.nominal_class.rank < SafetyClass.D.rank,
    )
    ctx.class_d_authorized = True
    ctx.effective_class = SafetyClass.D


def guard_readonly_db(
    script_name: str,
    *,
    use_production: bool = False,
) -> str:
    return begin_script_guard(
        SafetyClass.A,
        script_name,
        use_production=use_production,
    )


def guard_local_write_db(
    script_name: str,
    *,
    allow_production: bool = False,
) -> str:
    return begin_script_guard(
        SafetyClass.B,
        script_name,
        allow_production=allow_production,
    )


def guard_destructive_db(
    script_name: str,
    *,
    allow_production: bool = False,
    operation: str = "destructive write",
) -> str:
    ctx = _get_context()
    if ctx is None:
        _set_context(
            _GuardContext(
                script_name=script_name,
                nominal_class=SafetyClass.D,
                effective_class=SafetyClass.D,
                allow_production=allow_production,
            )
        )
    elif allow_production:
        ctx.allow_production = True

    url = _authorize_class_d(
        script_name=script_name,
        allow_production=allow_production,
        operation=operation,
        nominal_class=SafetyClass.D,
        escalated=False,
    )
    ctx = _get_context()
    if ctx is not None:
        ctx.class_d_authorized = True
        ctx.effective_class = SafetyClass.D
    return url


def guard_registry_write_db(
    script_name: str,
    *,
    allow_production: bool = False,
) -> str:
    return begin_script_guard(
        SafetyClass.C,
        script_name,
        allow_production=allow_production,
    )


def guard_destructive_db_from_args(
    args: Namespace,
    *,
    script_name: str,
    operation: str = "destructive write",
    nominal_class: SafetyClass = SafetyClass.D,
) -> str:
    allow = bool(getattr(args, "allow_production", False))
    if nominal_class == SafetyClass.C:
        return begin_script_guard(SafetyClass.C, script_name, allow_production=allow)
    if nominal_class == SafetyClass.B:
        return begin_script_guard(SafetyClass.B, script_name, allow_production=allow)
    return guard_destructive_db(
        script_name=script_name,
        allow_production=allow,
        operation=operation,
    )


def guard_readonly_db_from_args(args: Namespace, *, script_name: str) -> str:
    use_production = bool(getattr(args, "use_production", False))
    return guard_readonly_db(script_name=script_name, use_production=use_production)
