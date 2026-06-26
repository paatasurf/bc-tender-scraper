from __future__ import annotations

import base64
import logging
import threading
from typing import Any

import jwt
import requests
from fastapi import HTTPException, Request
from jwt import PyJWKClient

from config.env import get_env

logger = logging.getLogger(__name__)

_DEFAULT_BACKEND_JWKS_URL = "https://api.clerk.com/v1/jwks"

COMPANY_INTEL_PATH_PREFIXES = (
    "/api/companies",
    "/api/arch-companies",
    "/api/competitive-intelligence",
    "/api/company-wiki",
)

UPGRADE_DETAIL = "Company Intelligence requires a Basic or Pro plan."
PAID_PLANS = frozenset({"basic", "pro", "admin"})

_jwk_clients_lock = threading.Lock()
_jwk_clients: dict[str, PyJWKClient] = {}


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


def _read_authorization_header(request: Request) -> str:
    value = request.headers.get("Authorization")
    if not value:
        return ""
    return value.strip() if isinstance(value, str) else str(value).strip()


def _extract_bearer_token(request: Request) -> str | None:
    auth_header = _read_authorization_header(request)
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header.removeprefix("Bearer ").strip()
    return token or None


def _require_bearer_token(request: Request) -> str:
    token = _extract_bearer_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    return token


def _strip_env_credential(value: str) -> str:
    return value.strip().strip('"').strip("'")


def _get_sk_secret() -> str:
    """Return a Clerk secret key (sk_test_* / sk_live_*), never a publishable key."""
    raw = _strip_env_credential(get_env("CLERK_SECRET_KEY"))
    if raw.startswith("sk_"):
        return raw
    return ""


def _get_publishable_key() -> str:
    """Return a Clerk publishable key (pk_test_* / pk_live_*)."""
    raw = _strip_env_credential(get_env("CLERK_PUBLISHABLE_KEY"))
    if raw.startswith("pk_"):
        return raw

    # Common misconfiguration: publishable key pasted into CLERK_SECRET_KEY.
    wrong_slot = _strip_env_credential(get_env("CLERK_SECRET_KEY"))
    if wrong_slot.startswith("pk_"):
        logger.warning(
            "CLERK_SECRET_KEY contains a publishable key (pk_*). "
            "Set sk_* in CLERK_SECRET_KEY for Backend API calls; "
            "set pk_* in CLERK_PUBLISHABLE_KEY for JWT verification."
        )
        return wrong_slot
    return ""


def _pem_public_key() -> str | None:
    """Optional PEM public key for networkless verification (CLERK_JWT_KEY)."""
    pem = get_env("CLERK_JWT_KEY")
    if not pem:
        return None
    return pem.replace("\\n", "\n")


def _authorized_parties() -> frozenset[str]:
    raw = get_env("CLERK_AUTHORIZED_PARTIES")
    if not raw:
        return frozenset()
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def _frontend_api_domain_from_publishable(publishable_key: str) -> str | None:
    """Decode the Clerk instance domain embedded in a publishable key."""
    try:
        parts = publishable_key.split("_", 2)
        if len(parts) < 3:
            return None
        encoded = parts[2]
        padding = "=" * (-len(encoded) % 4)
        domain = base64.urlsafe_b64decode(encoded + padding).decode("utf-8").rstrip("$")
        return domain or None
    except Exception as exc:
        logger.debug("Could not decode Clerk publishable key domain: %s", exc)
        return None


def _jwks_sources() -> list[tuple[str, dict[str, str] | None]]:
    """Ordered JWKS sources for session-token verification.

    Session tokens from Clerk frontend SDKs are signed with the instance's
    Frontend API keys (public JWKS). Backend API JWKS requires sk_* and is
    tried as a fallback when available.
    """
    sources: list[tuple[str, dict[str, str] | None]] = []

    custom = get_env("CLERK_JWKS_URL")
    if custom:
        headers: dict[str, str] | None = None
        sk = _get_sk_secret()
        if sk and "api.clerk.com" in custom:
            headers = {"Authorization": f"Bearer {sk}"}
        sources.append((custom, headers))

    publishable = _get_publishable_key()
    if publishable:
        domain = _frontend_api_domain_from_publishable(publishable)
        if domain:
            sources.append((f"https://{domain}/.well-known/jwks.json", None))

    sk = _get_sk_secret()
    if sk:
        sources.append((_DEFAULT_BACKEND_JWKS_URL, {"Authorization": f"Bearer {sk}"}))

    return sources


def _clerk_verification_available() -> bool:
    """True when the server can cryptographically verify Clerk session tokens."""
    return bool(
        _pem_public_key()
        or _get_sk_secret()
        or _get_publishable_key()
        or get_env("CLERK_JWKS_URL")
    )


def _get_jwk_client(url: str, headers: dict[str, str] | None) -> PyJWKClient:
    with _jwk_clients_lock:
        client = _jwk_clients.get(url)
        if client is None:
            client = PyJWKClient(url, headers=headers, cache_keys=True)
            _jwk_clients[url] = client
        return client


def _issuer_jwks_url(token: str) -> str | None:
    try:
        claims = jwt.decode(
            token,
            options={
                "verify_signature": False,
                "verify_exp": False,
                "verify_nbf": False,
                "verify_aud": False,
            },
        )
    except jwt.PyJWTError:
        return None

    iss = claims.get("iss") if isinstance(claims, dict) else None
    if not isinstance(iss, str) or not iss.startswith("https://"):
        return None
    return iss.rstrip("/") + "/.well-known/jwks.json"


def _resolve_signing_key(token: str) -> tuple[Any, list[str]]:
    pem = _pem_public_key()
    if pem:
        return pem, ["RS256"]

    errors: list[str] = []
    tried: set[str] = set()

    def _try_url(url: str, headers: dict[str, str] | None) -> tuple[Any, list[str]] | None:
        if not url or url in tried:
            return None
        tried.add(url)
        try:
            client = _get_jwk_client(url, headers)
            jwk = client.get_signing_key_from_jwt(token)
            algorithm = jwk.algorithm_name or "RS256"
            return jwk.key, [algorithm]
        except Exception as exc:
            errors.append(f"{url}: {exc}")
            logger.debug("Clerk JWKS lookup failed url=%s err=%s", url, exc)
            return None

    for url, headers in _jwks_sources():
        resolved = _try_url(url, headers)
        if resolved is not None:
            return resolved

    iss_url = _issuer_jwks_url(token)
    if iss_url:
        resolved = _try_url(iss_url, None)
        if resolved is not None:
            return resolved

    logger.warning(
        "Clerk JWT verification: no signing key found (sources=%s errors=%s)",
        list(tried),
        errors[:3],
    )
    raise HTTPException(status_code=401, detail="Invalid authorization token")


def _verify_jwt(token: str) -> dict[str, Any]:
    """Verify a Clerk session token's signature and return its claims."""
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid authorization token") from exc

    alg = header.get("alg")
    if not isinstance(alg, str) or alg.lower() == "none":
        raise HTTPException(status_code=401, detail="Invalid authorization token")

    try:
        signing_key, algorithms = _resolve_signing_key(token)
        payload = jwt.decode(
            token,
            signing_key,
            algorithms=algorithms,
            leeway=5,
            options={"verify_aud": False},
        )
    except HTTPException:
        raise
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="Token expired") from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid authorization token") from exc
    except Exception as exc:
        logger.exception("Clerk JWT verification failed unexpectedly")
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
    secret = _get_sk_secret()
    if not secret:
        logger.debug(
            "Clerk plan check: no sk_* secret configured; skipping user metadata lookup"
        )
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

    try:
        payload = resp.json()
    except ValueError as exc:
        logger.warning("Clerk user lookup returned non-JSON for %s", user_id)
        raise HTTPException(status_code=401, detail="Invalid authorization token") from exc

    metadata = _extract_public_metadata(payload if isinstance(payload, dict) else {})
    logger.info(
        "Clerk plan check: user=%s api_metadata=%s api_plan=%s",
        user_id,
        metadata,
        _plan_from_metadata(metadata),
    )
    return metadata


def get_user_plan(request: Request) -> str:
    """Resolve the caller's subscription plan from a verified Clerk session token."""
    auth_header = _read_authorization_header(request)
    logger.info(
        "Clerk plan check: path=%s authorization_present=%s bearer=%s",
        request.url.path,
        bool(auth_header),
        auth_header.startswith("Bearer "),
    )
    token = _require_bearer_token(request)

    logger.info(
        "Clerk plan check: path=%s token_length=%s",
        request.url.path,
        len(token),
    )

    try:
        payload = _verify_jwt(token)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Clerk plan check failed while verifying JWT for %s", request.url.path)
        raise HTTPException(status_code=401, detail="Invalid authorization token") from exc

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


# Backwards-compatible alias used by existing imports.
get_plan_from_request = get_user_plan


def assert_company_intelligence_access(request: Request) -> None:
    # Fail fast on missing auth before any JWT/JWKS work that could raise
    # uncaught exceptions from sync code invoked by async middleware.
    _require_bearer_token(request)

    if not _clerk_verification_available():
        logger.error(
            "Clerk plan gate FAIL-CLOSED for %s: configure CLERK_SECRET_KEY (sk_*), "
            "CLERK_PUBLISHABLE_KEY (pk_*), CLERK_JWT_KEY, or CLERK_JWKS_URL",
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

    try:
        plan = get_user_plan(request)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Clerk plan gate failed unexpectedly for %s", request.url.path)
        raise HTTPException(status_code=401, detail="Invalid authorization token") from exc

    allowed = plan in PAID_PLANS
    logger.info(
        "Clerk plan gate: path=%s plan=%s allowed=%s",
        request.url.path,
        plan,
        allowed,
    )
    if not allowed:
        raise HTTPException(status_code=403, detail=UPGRADE_DETAIL)
