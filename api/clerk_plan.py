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


def _fetch_clerk_public_metadata(user_id: str) -> dict[str, Any]:
    secret = get_env("CLERK_SECRET_KEY")
    if not secret:
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
        raise HTTPException(status_code=401, detail="Invalid authorization token")

    payload = resp.json()
    metadata = payload.get("public_metadata")
    return metadata if isinstance(metadata, dict) else {}


def get_plan_from_request(request: Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")

    token = auth_header.removeprefix("Bearer ").strip()
    payload = _decode_jwt_payload(token)

    exp = payload.get("exp")
    if isinstance(exp, (int, float)) and exp < time.time():
        raise HTTPException(status_code=401, detail="Token expired")

    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        raise HTTPException(status_code=401, detail="Invalid authorization token")

    metadata = payload.get("public_metadata")
    if not isinstance(metadata, dict) or "plan" not in metadata:
        metadata = _fetch_clerk_public_metadata(sub)

    return _normalize_plan(metadata.get("plan"))


def assert_company_intelligence_access(request: Request) -> None:
    if not get_env("CLERK_SECRET_KEY"):
        return

    plan = get_plan_from_request(request)
    if plan == "free":
        raise HTTPException(status_code=403, detail=UPGRADE_DETAIL)
