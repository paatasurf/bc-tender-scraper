"""Configuration for Google enrichment service."""

from __future__ import annotations

from dataclasses import dataclass

from config.env import get_env


@dataclass(frozen=True)
class GoogleEnrichmentSettings:
    provider: str
    provider_fallback: str
    apify_actor_id: str
    oss_scraper_url: str
    stale_days: int
    batch_size: int
    confidence_accept: float
    confidence_review: float
    no_match_retry_days: int
    copy_website_to_website: bool


def _int_env(name: str, default: int) -> int:
    raw = get_env(name, str(default))
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    raw = get_env(name, str(default))
    try:
        return float(raw)
    except ValueError:
        return default


def load_settings() -> GoogleEnrichmentSettings:
    copy_flag = get_env("GOOGLE_COPY_WEBSITE_TO_WEBSITE", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    return GoogleEnrichmentSettings(
        provider=get_env("GOOGLE_PROVIDER", "apify").lower(),
        provider_fallback=get_env("GOOGLE_PROVIDER_FALLBACK", "oss").lower(),
        apify_actor_id=get_env("APIFY_ACTOR_ID", "compass/google-maps-extractor"),
        oss_scraper_url=get_env("OSS_SCRAPER_URL", ""),
        stale_days=_int_env("GOOGLE_ENRICHMENT_STALE_DAYS", 30),
        batch_size=_int_env("GOOGLE_ENRICHMENT_BATCH_SIZE", 21),
        confidence_accept=_float_env("GOOGLE_ENRICHMENT_CONFIDENCE_ACCEPT", 0.70),
        confidence_review=_float_env("GOOGLE_ENRICHMENT_CONFIDENCE_REVIEW", 0.55),
        no_match_retry_days=_int_env("GOOGLE_ENRICHMENT_NO_MATCH_RETRY_DAYS", 90),
        copy_website_to_website=copy_flag,
    )
