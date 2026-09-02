"""Shared-secret auth for the enrichment worker's HTTP endpoints
(docs/COMPANY_CONTACT_PROVIDER_PHASE3C_EXECUTION_PLAN.md S4.2).

Mirrors api/internal.py's _require_internal_key() exactly: a dedicated
header, hmac.compare_digest() for constant-time comparison, fail-closed
when the expected secret is unset server-side (never a default/empty-
string bypass -- an unset ENRICHMENT_WORKER_API_KEY means every request is
rejected, not that auth is skipped). ENRICHMENT_WORKER_API_KEY is
deliberately a SEPARATE secret from INTERNAL_API_KEY (S4.2) -- compromise
of one must not imply compromise of the other.

Applied to /lookup only -- /health is deliberately unauthenticated
(S4.3): Railway's own healthcheck prober cannot supply a secret header,
and that route does nothing beyond reporting liveness (no DB access, no
fetch capability, no company data reachable from it).
"""

from __future__ import annotations

import hmac
import os

from fastapi import HTTPException, Request

WORKER_AUTH_HEADER = "X-Enrichment-Worker-Key"
WORKER_AUTH_ENV_VAR = "ENRICHMENT_WORKER_API_KEY"


def require_worker_api_key(request: Request) -> None:
    """FastAPI dependency -- raises 403 (never a distinguishing status
    code for "secret unset" vs. "wrong key", matching _require_internal_key()'s
    own fail-closed, non-leaky behavior) unless the caller's header exactly
    matches the server-configured secret."""
    expected = os.getenv(WORKER_AUTH_ENV_VAR)
    if not expected:
        raise HTTPException(status_code=403, detail="Forbidden")
    key = request.headers.get(WORKER_AUTH_HEADER)
    if key is None or not hmac.compare_digest(key, expected):
        raise HTTPException(status_code=403, detail="Forbidden")
