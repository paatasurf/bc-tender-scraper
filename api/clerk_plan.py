from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any

import requests
from fastapi import HTTPException, Request

from config.env import get_env

logger = logging.getLogger(__name__)

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


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("invalid token structure")
        payload_b64 = parts[1]
        padding = "=" * (-len(payload_b64) % 4)
        raw = base64.urlsafe_b64decode(payload_b64 + padding)
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("invalid token payload")
        return parsed
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid authorization token") from exc


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
    payload = _decode_jwt_payload(token)

    exp = payload.get("exp")
    if isinstance(exp, (int, float)) and exp < time.time():
        logger.info("Clerk plan check: expired token for %s", request.url.path)
        raise HTTPException(status_code=401, detail="Token expired")

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
    if not get_env("CLERK_SECRET_KEY"):
        logger.debug("Clerk plan gate skipped: CLERK_SECRET_KEY not configured")
        return

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
