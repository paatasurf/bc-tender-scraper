from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

import jwt
import requests
from fastapi import HTTPException, Request

from config.env import get_env

logger = logging.getLogger(__name__)

# Clerk JWKS (public signing keys) cache. Keys rarely rotate, so a short-lived
# in-memory cache keeps verification networkless on the hot path after warm-up.
_DEFAULT_JWKS_URL = "https://api.clerk.com/v1/jwks"
_JWKS_TTL_SECONDS = 600
_jwks_lock = threading.Lock()
_jwks_keys: dict[str, Any] = {}
_jwks_fetched_at: float = 0.0

COMPANY_INTEL_PATH_PREFIXES = (
    "/api/companies",
    "/api/arch-companies",
    "/api/competitive-intelligence",
    "/api/company-wiki",
)

UPGRADE_DETAIL = "Company Intelligence requires a Basic or Pro plan."
PAID_PLANS = frozenset({"basic", "pro", "admin"})


def is_company_intelligence_path(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in COMPANY_INTEL_PATH_PREFIXES)


def requires_company_intelligence_access(request: Request) -> bool:
    path = request.url.path
    if is_company_intelligence_path(path):
        return True
    if path == "/api/early-signals" and request.query_params.get("company_id"):
        return True
    if path == "/api/contract-awards" and request.query_params.get("company_id"):
        return True
    if path == "/api/ai-matching":
        return True
    return False


def _jwks_url() -> str:
    return get_env("CLERK_JWKS_URL") or _DEFAULT_JWKS_URL


def _pem_public_key() -> str | None:
    """Optional PEM public key for networkless verification (CLERK_JWT_KEY)."""
    pem = get_env("CLERK_JWT_KEY")
    if not pem:
        return None
    # Env vars often store the PEM with escaped newlines.
    return pem.replace("\\n", "\n")


def _authorized_parties() -> frozenset[str]:
    raw = get_env("CLERK_AUTHORIZED_PARTIES")
    if not raw:
        return frozenset()
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def _clerk_verification_available() -> bool:
    """True when the server can cryptographically verify Clerk tokens.

    Verification needs either a PEM public key (networkless) or a secret key to
    fetch the JWKS. When neither is configured we MUST fail closed.
    """
    return bool(_pem_public_key() or get_env("CLERK_SECRET_KEY"))


def _fetch_jwks() -> dict[str, Any]:
    url = _jwks_url()
    headers = {"Accept": "application/json"}
    secret = get_env("CLERK_SECRET_KEY")
    # The Backend API JWKS endpoint requires the secret key; the public
    # Frontend API (`/.well-known/jwks.json`) does not.
    if secret and "api.clerk.com" in url:
        headers["Authorization"] = f"Bearer {secret}"
    resp = requests.get(url, headers=headers, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError("Unexpected JWKS response")
    return data


def _load_signing_keys(*, force: bool = False) -> dict[str, Any]:
    global _jwks_fetched_at
    with _jwks_lock:
        is_fresh = (time.time() - _jwks_fetched_at) < _JWKS_TTL_SECONDS
        if _jwks_keys and is_fresh and not force:
            return dict(_jwks_keys)

        try:
            data = _fetch_jwks()
        except Exception as exc:
            logger.warning("Clerk JWKS fetch failed: %s", exc)
            # Fall back to whatever we already cached (may be empty).
            return dict(_jwks_keys)

        keys: dict[str, Any] = {}
        for entry in data.get("keys", []):
            kid = entry.get("kid")
            if not kid:
                continue
            try:
                keys[kid] = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(entry))
            except Exception as exc:  # malformed key entry — skip it
                logger.debug("Skipping unusable JWKS entry %s: %s", kid, exc)

        if keys:
            _jwks_keys.clear()
            _jwks_keys.update(keys)
            _jwks_fetched_at = time.time()
        return dict(_jwks_keys)


def _signing_key_for_kid(kid: str) -> Any | None:
    keys = _load_signing_keys()
    if kid not in keys:
        # Key may have rotated since the last fetch — refetch once.
        keys = _load_signing_keys(force=True)
    return keys.get(kid)


def _verify_jwt(token: str) -> dict[str, Any]:
    """Verify a Clerk session token's RS256 signature and return its claims.

    Raises HTTP 401 on any signature, structure, or expiry failure.
    """
    try:
        header = jwt.get_unverified_header(token)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid authorization token") from exc

    if header.get("alg") != "RS256":
        # Reject anything that isn't Clerk's RS256 (notably "none" / HS* downgrade).
        raise HTTPException(status_code=401, detail="Invalid authorization token")

    pem = _pem_public_key()
    if pem:
        signing_key: Any = pem
    else:
        kid = header.get("kid")
        if not kid:
            raise HTTPException(status_code=401, detail="Invalid authorization token")
        signing_key = _signing_key_for_kid(kid)
        if signing_key is None:
            logger.warning("Clerk JWT verification: no signing key for kid=%s", kid)
            raise HTTPException(status_code=401, detail="Invalid authorization token")

    try:
        payload = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            leeway=5,
            options={"verify_aud": False},
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="Token expired") from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid authorization token") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=401, detail="Invalid authorization token")

    allowed_parties = _authorized_parties()
    if allowed_parties:
        azp = payload.get("azp")
        if azp and azp not in allowed_parties:
            logger.warning("Clerk JWT verification: unauthorized party azp=%s", azp)
            raise HTTPException(status_code=401, detail="Invalid authorization token")

    return payload


def _normalize_plan(value: Any) -> str:
    if not isinstance(value, str):
        return "free"
    normalized = value.strip().lower()
    return normalized or "free"


def _extract_public_metadata(source: dict[str, Any]) -> dict[str, Any]:
    for key in ("public_metadata", "publicMetadata"):
        metadata = source.get(key)
        if isinstance(metadata, dict):
            return metadata
    return {}


def _plan_from_metadata(metadata: dict[str, Any]) -> str:
    return _normalize_plan(metadata.get("plan"))


def _role_from_metadata(metadata: dict[str, Any]) -> str:
    return _normalize_plan(metadata.get("role"))


def _has_paid_access(metadata: dict[str, Any]) -> bool:
    plan = _plan_from_metadata(metadata)
    role = _role_from_metadata(metadata)
    return plan in PAID_PLANS or role == "admin"


def _fetch_clerk_public_metadata(user_id: str) -> dict[str, Any]:
    secret = get_env("CLERK_SECRET_KEY")
    if not secret:
        logger.debug("Clerk plan check: CLERK_SECRET_KEY missing, skipping user lookup")
        return {}

    try:
        resp = requests.get(
            f"https://api.clerk.com/v1/users/{user_id}",
            headers={"Authorization": f"Bearer {secret}"},
            timeout=10,
        )
    except requests.RequestException as exc:
        logger.warning("Clerk user lookup failed for %s: %s", user_id, exc)
        raise HTTPException(status_code=401, detail="Invalid authorization token") from exc

    if resp.status_code != 200:
        logger.warning(
            "Clerk user lookup returned %s for %s: %s",
            resp.status_code,
            user_id,
            resp.text[:200],
        )
        raise HTTPException(status_code=401, detail="Invalid authorization token")

    payload = resp.json()
    metadata = _extract_public_metadata(payload if isinstance(payload, dict) else {})
    logger.info(
        "Clerk plan check: user=%s api_metadata=%s api_plan=%s",
        user_id,
        metadata,
        _plan_from_metadata(metadata),
    )
    return metadata


def get_plan_from_request(request: Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    logger.info(
        "Clerk plan check: path=%s authorization_present=%s bearer=%s",
        request.url.path,
        bool(auth_header),
        auth_header.startswith("Bearer "),
    )
    if not auth_header.startswith("Bearer "):
        logger.info("Clerk plan check: missing Authorization header for %s", request.url.path)
        raise HTTPException(status_code=401, detail="Authentication required")

    token = auth_header.removeprefix("Bearer ").strip()
    logger.info(
        "Clerk plan check: path=%s token_length=%s",
        request.url.path,
        len(token),
    )
    payload = _verify_jwt(token)

    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        logger.info("Clerk plan check: JWT missing sub claim for %s", request.url.path)
        raise HTTPException(status_code=401, detail="Invalid authorization token")

    jwt_metadata = _extract_public_metadata(payload)
    jwt_plan = _plan_from_metadata(jwt_metadata)
    jwt_role = _role_from_metadata(jwt_metadata)
    logger.info(
        "Clerk plan check: path=%s user=%s jwt_metadata=%s jwt_plan=%s jwt_role=%s jwt_allowed=%s",
        request.url.path,
        sub,
        jwt_metadata,
        jwt_plan,
        jwt_role,
        _has_paid_access(jwt_metadata),
    )

    if _has_paid_access(jwt_metadata):
        return jwt_plan if jwt_plan in PAID_PLANS else jwt_role

    api_metadata = _fetch_clerk_public_metadata(sub)
    api_plan = _plan_from_metadata(api_metadata)
    api_role = _role_from_metadata(api_metadata)
    logger.info(
        "Clerk plan check: path=%s user=%s resolved_plan=%s resolved_role=%s api_allowed=%s (jwt_plan=%s)",
        request.url.path,
        sub,
        api_plan,
        api_role,
        _has_paid_access(api_metadata),
        jwt_plan,
    )
    return api_plan if api_plan in PAID_PLANS else api_role


def assert_company_intelligence_access(request: Request) -> None:
    # FAIL CLOSED: without a way to cryptographically verify Clerk tokens we
    # cannot trust any plan claim, so deny access instead of granting it.
    if not _clerk_verification_available():
        logger.error(
            "Clerk plan gate FAIL-CLOSED for %s: neither CLERK_SECRET_KEY nor "
            "CLERK_JWT_KEY is configured",
            request.url.path,
        )
        raise HTTPException(
            status_code=503,
            detail="Authentication is not configured on the server.",
        )

    auth_header = request.headers.get("Authorization", "")
    logger.info(
        "Clerk plan gate: path=%s method=%s authorization_present=%s",
        request.url.path,
        request.method,
        bool(auth_header),
    )

    plan = get_plan_from_request(request)
    allowed = plan in PAID_PLANS
    logger.info(
        "Clerk plan gate: path=%s plan=%s allowed=%s",
        request.url.path,
        plan,
        allowed,
    )
    if not allowed:
        raise HTTPException(status_code=403, detail=UPGRADE_DETAIL)
