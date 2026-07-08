#!/usr/bin/env python3
"""Verify persistent LinkedIn browser profile can reach the feed."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.linkedin.paths import BROWSER_PROFILE_DIR  # noqa: E402
from research.linkedin.session import ProfileExpiredError, is_login_url  # noqa: E402


async def verify_profile(*, headless: bool = True) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_PROFILE_DIR),
            headless=headless,
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2000)
            if is_login_url(page.url):
                raise ProfileExpiredError(
                    f"Profile not authenticated (landed on {page.url}). "
                    "Close all Chromium windows, run login_profile.py again, then re-check."
                )
            print(f"OK: authenticated feed reachable at {page.url}")
        finally:
            await context.close()


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Verify LinkedIn persistent profile session.")
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    try:
        asyncio.run(verify_profile(headless=args.headless))
    except ProfileExpiredError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
