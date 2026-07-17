"""Write-authorization capability for the Classification Claims Gateway.

Deliberately separated from ``gateway.py``: that module documents itself as
never touching ``db.db_safety`` or resolving a production URL on its own —
this module is the one, explicit exception. ``gateway.py`` only imports the
:class:`ClaimsWriteCapability` type and the package-internal
:func:`_unwrap_engine` helper from here — never
:func:`acquire_claims_write_capability` itself and never ``db.db_safety``.

**What this actually guards against.** This is a *fail-closed barrier
against accidentally bypassing the public API*, not an absolute,
unforgeable security boundary — Python has no such thing at the language
level, and nothing here claims otherwise. A caller who writes
``submit_claim(some_engine, dry_run=False)`` with a plain ``Engine`` gets a
clear, typed refusal (:class:`UnauthorizedWriteError`) before any SQL runs,
instead of silently writing to whatever database that engine happens to
point at. A caller who deliberately reaches into this module's
underscore-prefixed internals (``_CAPABILITY_TOKEN``, ``_unwrap_engine``)
can defeat that check — that is an explicit, visible act of bypassing the
package's own contract, not something this module can prevent from Python
code running in the same process. The value this provides is making the
*normal, accidental* path (passing a bare ``Engine`` where a capability is
expected, or trying to skip :func:`acquire_claims_write_capability` and its
guard call) fail loudly and immediately, not making misuse impossible for a
determined caller with source access.

:class:`ClaimsWriteCapability` also does not expose its wrapped ``Engine``
publicly (no ``.engine`` attribute) — only :func:`_unwrap_engine`, imported
by ``gateway.py`` from within this same package, can reach it. This is the
same fail-closed-against-accidents framing: it stops a caller from grabbing
the underlying ``Engine`` off a capability and using it directly (bypassing
every check ``submit_claim``/``record_event`` perform), without claiming to
make that impossible for code that imports the private helper directly.

:func:`acquire_claims_write_capability` is the only way to construct a
:class:`ClaimsWriteCapability`:

1. It runs the existing Class C/D guard (``db.db_safety.guard_destructive_db``)
   exactly once — for a production target this requires a human typing the
   confirmation phrase at a real interactive TTY (agents/CI cannot satisfy
   it; see ``db/db_safety.py``). If the guard refuses, it raises
   ``SystemExit`` and this function never returns — no capability escapes a
   failed or absent confirmation.
2. It builds a fresh ``Engine`` from the guard-approved URL and seals it
   inside a frozen ``ClaimsWriteCapability``.

Acquire ONE capability per batch and reuse it across many
``submit_claim(..., dry_run=False)`` / ``record_event(...)`` calls — the
guard must not run per claim. Because the dataclass is frozen, the wrapped
``Engine`` can never be reassigned through normal attribute access after
construction; a caller who wants a different target must acquire a whole
new capability (and pass the guard again to get one).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

__all__ = [
    "ClaimsWriteCapability",
    "UnauthorizedWriteError",
    "acquire_claims_write_capability",
]

# Module-private; deliberately not exported (absent from __all__ and from
# claims/__init__.py). Reaching this from outside the module requires an
# explicit, visible `from ...gateway_capability import _CAPABILITY_TOKEN` --
# not a call-site typo like `authorized=True`. See module docstring for what
# this is (and is not) a guarantee against.
_CAPABILITY_TOKEN = object()


class UnauthorizedWriteError(RuntimeError):
    """Raised when a Gateway write path is invoked without a genuine
    :class:`ClaimsWriteCapability` -- either a raw ``Engine`` (or anything
    else) was passed where a capability is required, or something attempted
    to construct :class:`ClaimsWriteCapability` directly instead of through
    :func:`acquire_claims_write_capability`."""


@dataclass(frozen=True)
class ClaimsWriteCapability:
    """Proof that the Class C/D production guard has already run for this
    batch. Do not construct directly -- use
    :func:`acquire_claims_write_capability`. Exposes no public attributes
    (no ``.engine``) -- see module docstring for why and for what this
    protects against."""

    _engine: Engine
    _token: object = field(repr=False)

    def __post_init__(self) -> None:
        if self._token is not _CAPABILITY_TOKEN:
            raise UnauthorizedWriteError(
                "ClaimsWriteCapability cannot be constructed directly -- use "
                "acquire_claims_write_capability(), which requires the existing "
                "Class C/D guard to succeed first."
            )


def _unwrap_engine(capability: ClaimsWriteCapability) -> Engine:
    """Package-internal only -- not part of the public capability API.
    Used by ``gateway.py`` (within this same package) to reach the wrapped
    ``Engine`` at the point of executing a guarded write. Importing this
    from outside the package is an explicit, visible act of reaching into
    implementation internals; see module docstring."""
    if not isinstance(capability, ClaimsWriteCapability):
        raise UnauthorizedWriteError(
            f"_unwrap_engine() requires a ClaimsWriteCapability, got: "
            f"{type(capability).__name__}"
        )
    return capability._engine


def acquire_claims_write_capability(
    script_name: str,
    *,
    allow_production: bool = False,
    operation: str = "classification claims Gateway write",
) -> ClaimsWriteCapability:
    """The only sanctioned way to obtain write authorization for
    ``submit_claim(..., dry_run=False)`` / ``record_event(...)``. Call this
    ONCE per batch, not once per claim/event.

    Runs ``db.db_safety.guard_destructive_db`` exactly once. On any refusal
    (non-production ``DATABASE_URL`` used incorrectly, or a failed/absent
    human TTY confirmation for a production target) it raises
    ``SystemExit`` and this function never returns -- no capability is ever
    constructed for a failed confirmation.
    """
    from db.db_safety import guard_destructive_db

    url = guard_destructive_db(
        script_name=script_name,
        allow_production=allow_production,
        operation=operation,
    )
    engine = create_engine(url, pool_pre_ping=True)
    return ClaimsWriteCapability(_engine=engine, _token=_CAPABILITY_TOKEN)
