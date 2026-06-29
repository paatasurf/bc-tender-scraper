"""Fetch and interpret canonical ExecutiveDecisionBrief from voice-n8n-agent."""

from __future__ import annotations

import logging
from typing import Any

import requests

from config.env import get_env

logger = logging.getLogger(__name__)

DEFAULT_VOICE_AGENT_URL = "https://voice-n8n-agent-production.up.railway.app"


def voice_agent_url() -> str:
    return get_env("VOICE_AGENT_URL", DEFAULT_VOICE_AGENT_URL).rstrip("/")


def fetch_company_executive_brief(company_id: int) -> dict[str, Any] | None:
    """Return latest cached ExecutiveDecisionBrief dict for a company, or None."""
    url = f"{voice_agent_url()}/api/intelligence/brief/company/{company_id}"
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        payload = resp.json()
        brief = payload.get("executive_decision_brief")
        if isinstance(brief, dict):
            return brief
    except requests.RequestException as exc:
        logger.warning(
            "[CanonicalBrief] Fetch failed company_id=%s: %s",
            company_id,
            exc,
        )
    return None


def disposition_for_entity(
    brief: dict[str, Any],
    *,
    entity_type: str,
    entity_id: int,
) -> str | None:
    """Lookup executive disposition without re-ranking."""
    candidates: list[dict[str, Any]] = []
    top = brief.get("top_opportunities") or {}
    candidates.extend(top.get("items") or [])
    candidates.extend(top.get("items_ignored") or [])
    candidates.extend(brief.get("ignored_opportunities") or [])

    for item in candidates:
        if (
            str(item.get("entity_type") or "") == entity_type
            and item.get("entity_id") == entity_id
        ):
            disp = str(item.get("disposition") or "").strip().lower()
            return disp or None
    return None


def opportunity_labels_by_disposition(brief: dict[str, Any]) -> dict[str, list[str]]:
    """Group opportunity labels by disposition — canonical ordering helper."""
    buckets: dict[str, list[str]] = {
        "pursue": [],
        "prepare": [],
        "monitor": [],
        "ignore": [],
    }
    seen: set[str] = set()

    def _collect(items: list[dict[str, Any]] | None) -> None:
        if not items:
            return
        for item in items:
            label = str(item.get("label") or "").strip()
            disp = str(item.get("disposition") or "").strip().lower()
            if not label or disp not in buckets or label in seen:
                continue
            seen.add(label)
            buckets[disp].append(label)

    top = brief.get("top_opportunities") or {}
    _collect(top.get("items"))
    _collect(top.get("items_ignored"))
    _collect(brief.get("ignored_opportunities"))
    return buckets
