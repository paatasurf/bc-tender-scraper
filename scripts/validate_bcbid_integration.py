"""Validate BC Bid integration wiring and optionally run a live scrape smoke test."""

from __future__ import annotations

import importlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "bcbid-integration-validation.json"
sys.path.insert(0, str(ROOT))


def _check(name: str, ok: bool, detail: str = "") -> dict:
    return {"check": name, "ok": ok, "detail": detail}


def _cookies_configured() -> bool:
    cookie_file = ROOT / "bcbid_cookies.txt"
    if cookie_file.exists():
        for line in cookie_file.read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#") and len(line.split("\t")) == 7:
                return True
    content = os.environ.get("BCBID_COOKIES_CONTENT", "").strip()
    return bool(content)


def main() -> int:
    checks: list[dict] = []

    for module_name in (
        "scraper.bcbid",
        "scraper.bcbid_common",
        "scraper.tender_merge",
        "scraper.runners",
    ):
        try:
            importlib.import_module(module_name)
            checks.append(_check(f"import:{module_name}", True))
        except Exception as exc:
            checks.append(_check(f"import:{module_name}", False, str(exc)))

    from scraper.models import Tender
    from scraper.tender_merge import merge_tenders_by_url, split_tenders_by_source

    federal = Tender(
        title="Federal",
        organization="Gov",
        category="Construction",
        posted_date="",
        closing_date="",
        estimated_value="",
        location="BC",
        tender_id="1",
        url="https://buyandsell.gc.ca/t/1",
        source="buyandsell.gc.ca",
    )
    bcbid = Tender(
        title="Provincial",
        organization="City",
        category="Construction",
        posted_date="",
        closing_date="",
        estimated_value="",
        location="BC",
        tender_id="2",
        url="https://www.bcbid.gov.bc.ca/page.aspx/en/bpm/process_manage_extranet/2",
        source="bcbid.gov.bc.ca",
    )
    merged = merge_tenders_by_url([federal], [bcbid])
    checks.append(_check("merge_tenders_by_url", len(merged) == 2, f"count={len(merged)}"))
    split_federal, split_bcbid = split_tenders_by_source(merged)
    checks.append(
        _check(
            "split_tenders_by_source",
            len(split_federal) == 1 and len(split_bcbid) == 1,
            f"federal={len(split_federal)} bcbid={len(split_bcbid)}",
        )
    )

    internal = (ROOT / "api" / "internal.py").read_text(encoding="utf-8")
    checks.append(_check("endpoint:/internal/scrape/bcbid", "/scrape/bcbid" in internal))
    checks.append(_check("runner:run_bcbid_scraper", "def run_bcbid_scraper" in (ROOT / "scraper" / "runners.py").read_text(encoding="utf-8")))
    checks.append(_check("main:Federal + BC Bid step", "Federal + BC Bid tenders" in (ROOT / "scraper" / "main.py").read_text(encoding="utf-8")))
    checks.append(_check("auth:BCBID_COOKIES_CONTENT documented", "BCBID_COOKIES_CONTENT" in (ROOT / ".env.example").read_text(encoding="utf-8")))
    checks.append(_check("auth:bcbid_cookies.txt gitignored", "bcbid_cookies.txt" in (ROOT / ".gitignore").read_text(encoding="utf-8")))

    cookies_ok = _cookies_configured()
    checks.append(
        _check(
            "auth:cookies_configured",
            cookies_ok,
            "Set bcbid_cookies.txt or BCBID_COOKIES_CONTENT for live scrape",
        )
    )

    live: dict | None = None
    if cookies_ok and "--live" in sys.argv:
        import config.env  # noqa: F401
        from scraper.runners import run_bcbid_scraper

        live = run_bcbid_scraper()
        checks.append(
            _check(
                "live:run_bcbid_scraper",
                live.get("bcbid_saved", 0) > 0 and not live.get("bcbid_error"),
                json.dumps(live),
            )
        )

    passed = sum(1 for item in checks if item["ok"])
    wiring_passed = all(item["ok"] for item in checks if not item["check"].startswith("auth:cookies") and not item["check"].startswith("live:"))
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "passed": passed,
            "total": len(checks),
            "wiring_complete": wiring_passed,
            "ready_for_production": wiring_passed and cookies_ok,
            "blocked_by": [] if cookies_ok else ["BCBID_COOKIES_CONTENT or bcbid_cookies.txt"],
        },
        "checks": checks,
        "live_scrape": live,
        "workflow": {
            "daily_scheduler": "APScheduler job daily_scrape_import → run_pipeline.py → scraper.main.run (Federal + BC Bid step)",
            "n8n_combined": "POST /internal/scrape/federal (includes BC Bid merge)",
            "n8n_dedicated_bcbid": "POST /internal/scrape/bcbid → POST /internal/import",
        },
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if wiring_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
