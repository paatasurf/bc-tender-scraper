"""Unit tests for Google enrichment provider selection."""

from __future__ import annotations

import asyncio

import pytest

from pipeline.google_enrichment.config import GoogleEnrichmentSettings
from pipeline.google_enrichment.provider import (
    ApifyProvider,
    NullProvider,
    OpenSourceProvider,
    build_provider,
    get_fallback_provider,
    get_provider,
)


def _settings(**overrides) -> GoogleEnrichmentSettings:
    defaults = {
        "provider": "apify",
        "provider_fallback": "oss",
        "apify_actor_id": "compass/google-maps-extractor",
        "oss_scraper_url": "http://localhost:8080",
        "stale_days": 30,
        "batch_size": 21,
        "confidence_accept": 0.70,
        "confidence_review": 0.55,
        "no_match_retry_days": 90,
        "copy_website_to_website": False,
    }
    defaults.update(overrides)
    return GoogleEnrichmentSettings(**defaults)


def test_build_provider_apify():
    provider = build_provider(_settings(provider="apify"), "apify")
    assert isinstance(provider, ApifyProvider)
    assert provider.provider_name == "apify"


def test_build_provider_oss():
    provider = build_provider(_settings(), "oss")
    assert isinstance(provider, OpenSourceProvider)
    assert provider.provider_name == "oss"


def test_build_provider_none():
    provider = build_provider(_settings(), "none")
    assert isinstance(provider, NullProvider)


def test_build_provider_unknown_raises():
    with pytest.raises(ValueError, match="Unknown GOOGLE_PROVIDER"):
        build_provider(_settings(), "unknown-vendor")


def test_get_provider_uses_settings(monkeypatch):
    monkeypatch.setenv("GOOGLE_PROVIDER", "none")
    provider = get_provider()
    assert isinstance(provider, NullProvider)


def test_get_fallback_provider_uses_settings(monkeypatch):
    monkeypatch.setenv("GOOGLE_PROVIDER", "apify")
    monkeypatch.setenv("GOOGLE_PROVIDER_FALLBACK", "none")
    provider = get_fallback_provider()
    assert isinstance(provider, NullProvider)


def test_null_provider_lookup_returns_empty():
    async def _run() -> None:
        provider = NullProvider()
        assert await provider.lookup("Test Co Vancouver BC") == []
        assert await provider.healthcheck() is True

    asyncio.run(_run())


def test_apify_provider_requires_token_for_lookup():
    async def _run() -> None:
        provider = ApifyProvider(actor_id="compass/google-maps-extractor", token="")
        with pytest.raises(RuntimeError, match="APIFY_TOKEN"):
            await provider.lookup("query")

    asyncio.run(_run())


def test_apify_provider_healthcheck_false_without_token():
    async def _run() -> None:
        provider = ApifyProvider(actor_id="compass/google-maps-extractor", token="")
        assert await provider.healthcheck() is False

    asyncio.run(_run())
