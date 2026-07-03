"""Provider adapter interface and configuration-driven selection."""

from __future__ import annotations

from abc import ABC, abstractmethod

from pipeline.google_enrichment.apify_provider import ApifyProvider as ApifyMapsProvider
from pipeline.google_enrichment.config import GoogleEnrichmentSettings, load_settings
from pipeline.google_enrichment.models import PlaceCandidate


class GoogleEnrichmentProvider(ABC):
    """Abstract provider for Google Maps business lookups."""

    provider_name: str

    @abstractmethod
    async def lookup(self, query: str, *, limit: int = 3) -> list[PlaceCandidate]:
        """Return up to `limit` normalized candidates for one search query."""

    @abstractmethod
    async def healthcheck(self) -> bool:
        """Return True when the provider is reachable and configured."""


class OpenSourceProvider(GoogleEnrichmentProvider):
    """Self-hosted OSS scraper adapter (Phase 8)."""

    provider_name = "oss"

    def __init__(self, *, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    async def lookup(self, query: str, *, limit: int = 3) -> list[PlaceCandidate]:
        raise NotImplementedError("OpenSourceProvider.lookup is Phase 8")

    async def healthcheck(self) -> bool:
        return bool(self._base_url)


class NullProvider(GoogleEnrichmentProvider):
    """Dry-run provider for tests and disabled environments."""

    provider_name = "none"

    async def lookup(self, query: str, *, limit: int = 3) -> list[PlaceCandidate]:
        return []

    async def healthcheck(self) -> bool:
        return True


ApifyProvider = ApifyMapsProvider


def build_provider(settings: GoogleEnrichmentSettings, name: str) -> GoogleEnrichmentProvider:
    normalized = (name or "").lower()
    if normalized == "apify":
        from config.env import get_env

        return ApifyMapsProvider(actor_id=settings.apify_actor_id, token=get_env("APIFY_TOKEN"))
    if normalized == "oss":
        return OpenSourceProvider(base_url=settings.oss_scraper_url)
    if normalized in {"none", "null", ""}:
        return NullProvider()
    raise ValueError(f"Unknown GOOGLE_PROVIDER: {name!r}")


def get_provider(settings: GoogleEnrichmentSettings | None = None) -> GoogleEnrichmentProvider:
    """Return the primary provider from configuration."""
    cfg = settings or load_settings()
    return build_provider(cfg, cfg.provider)


def get_fallback_provider(settings: GoogleEnrichmentSettings | None = None) -> GoogleEnrichmentProvider:
    """Return the configured fallback provider."""
    cfg = settings or load_settings()
    return build_provider(cfg, cfg.provider_fallback)
