"""Tests for scraper/news_signals.py's M3F-3 on_phase callback -- the
module's own per-feed step order, dedup, saved CSV, and fail-and-continue
behavior, independent of pipeline/tender_data_pipeline.py's telemetry
wiring (see tests/unit/test_tender_data_pipeline.py for that layer).

Every test here patches _fetch_rss/create_session/save_csv_rows so no
real network or filesystem I/O happens.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests

from scraper.news_signals import scrape_news_signals


def _item(url: str, publisher: str) -> dict[str, str]:
    return {
        "title": f"Construction news from {publisher}",
        "text": "some construction development text",
        "publisher": publisher,
        "date": "2026-08-01",
        "url": url,
    }


def _patch_common():
    return (
        patch("scraper.news_signals.create_session", return_value=MagicMock()),
        patch("scraper.news_signals.save_csv_rows"),
    )


class _Ctx:
    def __init__(self, patches):
        self._patches = patches

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc_info):
        for p in reversed(self._patches):
            p.stop()


def _fetch_rss_all_success(_session, publisher, _feed_url):
    return [_item(f"https://example.test/{publisher}/1", publisher)]


def test_no_on_phase_kwarg_is_a_complete_no_op():
    """Calling scrape_news_signals() with no on_phase at all (the exact
    shape the manual/n8n /internal/scrape/news endpoint still uses) must
    behave exactly as before this change."""
    patches = list(_patch_common())
    patches.append(
        patch("scraper.news_signals._fetch_rss", side_effect=_fetch_rss_all_success)
    )
    with _Ctx(patches):
        signals = scrape_news_signals()

    assert len(signals) == 4  # one item per NEWS_SOURCES publisher


def test_on_phase_all_success_records_four_phases_in_order_no_failed():
    phases: list[str] = []
    patches = list(_patch_common())
    patches.append(
        patch("scraper.news_signals._fetch_rss", side_effect=_fetch_rss_all_success)
    )
    with _Ctx(patches):
        signals = scrape_news_signals(on_phase=phases.append)

    assert phases == [
        "business_in_vancouver",
        "daily_hive_vancouver",
        "vancouver_sun_business",
        "cbc_british_columbia",
    ]
    assert not any(p.endswith("_failed") for p in phases)
    assert len(signals) == 4


def test_one_feed_failure_reports_failed_phase_and_continues():
    phases: list[str] = []

    def flaky_fetch(_session, publisher, _feed_url):
        if publisher == "Daily Hive Vancouver":
            raise requests.RequestException("feed down")
        return _fetch_rss_all_success(_session, publisher, _feed_url)

    patches = list(_patch_common())
    patches.append(patch("scraper.news_signals._fetch_rss", side_effect=flaky_fetch))
    with _Ctx(patches):
        signals = scrape_news_signals(on_phase=phases.append)

    assert phases == [
        "business_in_vancouver",
        "daily_hive_vancouver_failed",
        "vancouver_sun_business",
        "cbc_british_columbia",
    ]
    # The other three feeds still ran; only the failed one contributed
    # nothing -- pre-existing print+continue fallback, unchanged.
    assert len(signals) == 3


def test_multiple_feed_failures_all_reported_as_failed_phases():
    phases: list[str] = []

    def flaky_fetch(_session, publisher, _feed_url):
        if publisher in ("Business in Vancouver", "CBC British Columbia"):
            raise requests.RequestException("feed down")
        return _fetch_rss_all_success(_session, publisher, _feed_url)

    patches = list(_patch_common())
    patches.append(patch("scraper.news_signals._fetch_rss", side_effect=flaky_fetch))
    with _Ctx(patches):
        signals = scrape_news_signals(on_phase=phases.append)

    failed_phases = [p for p in phases if p.endswith("_failed")]
    assert failed_phases == [
        "business_in_vancouver_failed",
        "cbc_british_columbia_failed",
    ]
    assert len(signals) == 2


def test_on_phase_exception_does_not_stop_feeds_or_change_result(caplog):
    call_order: list[str] = []

    def raising_on_phase(phase: str) -> None:
        call_order.append(phase)
        raise RuntimeError("callback exploded: sk_live_should_never_leak")

    patches = list(_patch_common())
    patches.append(
        patch("scraper.news_signals._fetch_rss", side_effect=_fetch_rss_all_success)
    )
    with _Ctx(patches):
        with caplog.at_level("WARNING"):
            signals = scrape_news_signals(on_phase=raising_on_phase)

    assert len(signals) == 4
    assert call_order == [
        "business_in_vancouver",
        "daily_hive_vancouver",
        "vancouver_sun_business",
        "cbc_british_columbia",
    ]
    assert "callback exploded" not in caplog.text
    assert "sk_live_should_never_leak" not in caplog.text
    for phase in call_order:
        assert f"[News] on_phase callback failed for phase={phase}" in caplog.text


def test_run_news_scraper_default_forwards_none_unchanged_behavior():
    """scraper.runners.run_news_scraper() called with no on_phase forwards
    on_phase=None to scrape_news_signals() -- functionally identical to
    the pre-M3F-3 zero-kwarg call (scrape_news_signals()'s own default is
    also None, guarded by `if on_phase is not None:` throughout). The
    real flag-off "zero kwargs at all" contract lives one layer up, in
    pipeline/tender_data_pipeline.py's gating (see
    tests/unit/test_tender_data_pipeline.py)."""
    from scraper.runners import run_news_scraper

    captured_kwargs = {}

    def fake_scrape(**kwargs):
        captured_kwargs.update(kwargs)
        return []

    with patch("scraper.runners.scrape_news_signals", side_effect=fake_scrape):
        result = run_news_scraper()

    assert captured_kwargs == {"on_phase": None}
    assert result == {"signals_saved": 0}


def test_run_news_scraper_passes_on_phase_through():
    from scraper.runners import run_news_scraper

    captured_kwargs = {}

    def fake_scrape(**kwargs):
        captured_kwargs.update(kwargs)
        return []

    def my_on_phase(_phase: str) -> None:
        pass

    with patch("scraper.runners.scrape_news_signals", side_effect=fake_scrape):
        run_news_scraper(on_phase=my_on_phase)

    assert captured_kwargs == {"on_phase": my_on_phase}
