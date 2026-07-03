"""Apify Google Maps actor adapter."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import requests

from pipeline.google_enrichment.models import PlaceCandidate

logger = logging.getLogger(__name__)

APIFY_BASE = "https://api.apify.com/v2"
DEFAULT_TIMEOUT_SECONDS = 120


def _actor_path(actor_id: str) -> str:
    return actor_id.replace("/", "~")


def _business_status(item: dict[str, Any]) -> str:
    if item.get("permanentlyClosed"):
        return "CLOSED_PERMANENTLY"
    if item.get("temporarilyClosed"):
        return "CLOSED_TEMPORARILY"
    return str(item.get("businessStatus") or item.get("business_status") or "OPERATIONAL")


def normalize_apify_item(item: dict[str, Any]) -> PlaceCandidate | None:
    place_id = str(item.get("placeId") or item.get("place_id") or "").strip()
    name = str(item.get("title") or item.get("name") or "").strip()
    if not place_id or not name:
        return None

    location = item.get("location") or {}
    lat = location.get("lat") if isinstance(location, dict) else None
    lng = location.get("lng") if isinstance(location, dict) else None

    return PlaceCandidate(
        place_id=place_id,
        name=name,
        rating=_optional_float(item.get("totalScore") or item.get("rating")),
        review_count=_optional_int(item.get("reviewsCount") or item.get("review_count")),
        category=str(item.get("categoryName") or item.get("category") or "")[:200],
        formatted_address=str(item.get("address") or item.get("formattedAddress") or "")[:500],
        phone=str(item.get("phone") or item.get("phoneUnformatted") or "")[:50],
        google_maps_url=str(item.get("url") or item.get("googleMapsUrl") or "")[:500],
        google_website=str(item.get("website") or "")[:500],
        business_status=_business_status(item),
        lat=_optional_float(lat),
        lng=_optional_float(lng),
        raw=item,
    )


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class ApifyProvider:
    """Apify Google Maps actor adapter."""

    provider_name = "apify"

    def __init__(
        self,
        *,
        actor_id: str,
        token: str = "",
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._actor_id = actor_id
        self._token = token
        self._timeout_seconds = timeout_seconds

    async def lookup(self, query: str, *, limit: int = 3) -> list[PlaceCandidate]:
        if not self._token:
            raise RuntimeError("APIFY_TOKEN is not configured")
        if not query.strip():
            return []

        payload = {
            "searchStringsArray": [query.strip()],
            "maxCrawledPlacesPerSearch": max(1, min(limit, 10)),
            "language": "en",
            "maxReviews": 0,
            "maxImages": 0,
            "scrapeReviewsPersonalData": False,
        }
        items = await asyncio.to_thread(self._run_sync, payload)
        candidates: list[PlaceCandidate] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            candidate = normalize_apify_item(item)
            if candidate is not None:
                candidates.append(candidate)
            if len(candidates) >= limit:
                break
        return candidates

    async def healthcheck(self) -> bool:
        if not self._token:
            return False
        return await asyncio.to_thread(self._healthcheck_sync)

    def _run_sync(self, payload: dict[str, Any]) -> list[Any]:
        url = (
            f"{APIFY_BASE}/acts/{_actor_path(self._actor_id)}"
            "/run-sync-get-dataset-items"
        )
        response = requests.post(
            url,
            params={"token": self._token},
            json=payload,
            timeout=self._timeout_seconds,
        )
        if response.status_code >= 400:
            detail = response.text[:500]
            raise RuntimeError(f"Apify actor failed ({response.status_code}): {detail}")
        data = response.json()
        if isinstance(data, list):
            return data
        return []

    def _healthcheck_sync(self) -> bool:
        response = requests.get(
            f"{APIFY_BASE}/users/me",
            params={"token": self._token},
            timeout=15,
        )
        return response.status_code == 200
