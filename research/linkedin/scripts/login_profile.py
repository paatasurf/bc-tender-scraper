#!/usr/bin/env python3
"""First-time login or profile refresh for persistent LinkedIn browser profile."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.linkedin.paths import BROWSER_PROFILE_DIR  # noqa: E402
from research.linkedin.session import is_login_url  # noqa: E402


async def login_profile(*, timeout_seconds: int = 300) -> None:
    from playwright.async_api import async_playwright

    BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    print("Launching persistent Chromium profile.", flush=True)
    print(f"Profile directory:\n  {BROWSER_PROFILE_DIR}", flush=True)
    print("\nLog into LinkedIn manually (MFA is OK). Do not enter credentials here.", flush=True)

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_PROFILE_DIR),
            headless=False,
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")

        print(
            f"\nWaiting up to {timeout_seconds}s for login "
            "(leave /login and reach your feed)...",
            flush=True,
        )

        elapsed = 0
        while elapsed < timeout_seconds * 1000:
            if not is_login_url(page.url) and "linkedin.com" in page.url.lower():
                break
            await page.wait_for_timeout(1000)
            elapsed += 1000
        else:
            print("\nStill waiting — press Enter when you see your LinkedIn feed.", flush=True)
            await asyncio.to_thread(input)

        if is_login_url(page.url):
            print(
                "\nStill on a login page. Finish logging in, then run this script again.",
                flush=True,
            )
            await context.close()
            raise SystemExit(1)

        await context.close()

    print("\nProfile saved. Future batches reuse this login automatically.", flush=True)
    print("\nRun a batch:\n  python research/linkedin/run_batch.py", flush=True)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Manual LinkedIn login into persistent browser profile.")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    try:
        asyncio.run(login_profile(timeout_seconds=args.timeout))
    except KeyboardInterrupt:
        print("\nCancelled.", flush=True)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
