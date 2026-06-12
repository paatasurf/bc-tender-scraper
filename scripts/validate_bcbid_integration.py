"""Validate BC Bid integration wiring and optionally run a live scrape smoke test."""

from __future__ import annotations

import importlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "bcbid-integration-validation.json"
sys.path.insert(0, str(ROOT))


def _check(name: str, ok: bool, detail: str = "") -> dict:
    return {"check": name, "ok": ok, "detail": detail}


def main() -> int:
    checks: list[dict] = []

    for module_name in (
        "scraper.bcbid",
        "scraper.bcbid_common",
        "scraper.bcbid_http",
        "scraper.tender_merge",
        "scraper.runners",
    ):
        try:
            importlib.import_module(module_name)
            checks.append(_check(f"import:{module_name}", True))
        except Exception as exc:
            checks.append(_check(f"import:{module_name}", False, str(exc)))

    try:
        from curl_cffi import requests as curl_requests  # noqa: F401

        checks.append(_check("deps:curl_cffi", True))
    except ImportError as exc:
        checks.append(_check("deps:curl_cffi", False, str(exc)))

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
    checks.append(
        _check(
            "http:curl_cffi_impersonate",
            'impersonate' in (ROOT / "scraper" / "bcbid_http.py").read_text(encoding="utf-8"),
        )
    )

    live: dict | None = None
    if "--live" in sys.argv:
        import config.env  # noqa: F401
        from scraper.bcbid_auth import BcbidSessionExpiredError
        from scraper.bcbid_browser import bootstrap_bcbid_session
        from scraper.bcbid_common import iter_browse_pages, parse_grid_row
        from scraper.bcbid_http import create_bcbid_session

        session = create_bcbid_session()
        try:
            bootstrap_bcbid_session(session)
            first_page = next(iter_browse_pages(session))
            grid = first_page.find("table", id="body_x_grid_grd")
            rows = []
            if grid:
                for row in grid.find_all("tr", attrs={"data-id": True}):
                    parsed = parse_grid_row(row)
                    if parsed:
                        rows.append(parsed)
            live = {"listing_rows_page1": len(rows), "sample_title": rows[0]["title"] if rows else None}
            checks.append(
                _check(
                    "live:listing_page",
                    len(rows) > 0,
                    json.dumps(live),
                )
            )
        except BcbidSessionExpiredError as exc:
            live = {"error": str(exc.reason)}
            checks.append(_check("live:listing_page", False, str(exc.reason)))
        except RuntimeError as exc:
            live = {"error": str(exc)}
            checks.append(_check("live:listing_page", False, str(exc)))

    passed = sum(1 for item in checks if item["ok"])
    wiring_passed = all(item["ok"] for item in checks if not item["check"].startswith("live:"))
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "passed": passed,
            "total": len(checks),
            "wiring_complete": wiring_passed,
            "ready_for_production": wiring_passed and (live is None or live.get("listing_rows_page1", 0) > 0),
            "blocked_by": [],
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
