#!/usr/bin/env python3
"""Create or refresh LinkedIn Playwright storageState (manual login, no passwords stored)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.linkedin.paths import DEFAULT_SESSION_PATH  # noqa: E402
from research.linkedin.session import is_login_url  # noqa: E402


async def _create_session(*, output_path: Path, timeout_seconds: int = 300) -> None:
    from playwright.async_api import async_playwright

    output_path.parent.mkdir(parents=True, exist_ok=True)

    print("Launching Chromium — log into LinkedIn in the browser window.", flush=True)
    print("Complete MFA if prompted. Do not close the browser.", flush=True)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")

        print(
            f"\nWaiting up to {timeout_seconds}s for login "
            "(URL should leave /login and show your feed)...",
            flush=True,
        )

        deadline_ms = timeout_seconds * 1000
        poll_ms = 1000
        elapsed = 0
        while elapsed < deadline_ms:
            if not is_login_url(page.url) and "linkedin.com" in page.url.lower():
                break
            await page.wait_for_timeout(poll_ms)
            elapsed += poll_ms
        else:
            print(
                "\nTimed out waiting for login. If you are logged in, press Enter to save anyway.",
                flush=True,
            )
            input()

        if is_login_url(page.url):
            print(
                "\nStill on a login page — session may not work. "
                "Finish logging in, then run this script again.",
                flush=True,
            )
            await browser.close()
            raise SystemExit(1)

        await context.storage_state(path=str(output_path))
        await browser.close()

    print(f"\nSaved authenticated session to:\n  {output_path}", flush=True)
    print(
        "\nOptional — point the pipeline at this file:\n"
        f'  $env:LINKEDIN_SESSION_PATH = "{output_path}"',
        flush=True,
    )


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Log into LinkedIn once and save Playwright storageState locally."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_SESSION_PATH,
        help=f"Output path (default: {DEFAULT_SESSION_PATH})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Seconds to wait for manual login (default 300).",
    )
    args = parser.parse_args()

    try:
        asyncio.run(_create_session(output_path=args.output, timeout_seconds=args.timeout))
    except KeyboardInterrupt:
        print("\nCancelled.", flush=True)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
