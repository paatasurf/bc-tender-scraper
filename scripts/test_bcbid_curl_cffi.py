"""Smoke test: curl_cffi fetch + BC Bid grid parsing."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bs4 import BeautifulSoup

from scraper.bcbid_common import parse_grid_row
from scraper.bcbid_http import bcbid_get, create_bcbid_session
from scraper.config import BCBID_BROWSE_URL
from scraper.utils import is_browser_check

SAMPLE_ROW_HTML = """
<tr id="body_x_grid_grd_tr_230554" data-object-type="rfp" data-id="230554">
<td data-iv-role="cell">Open</td>
<td data-iv-role="cell"><a href="/page.aspx/en/bpm/process_manage_extranet/230554">RFP26 239</a></td>
<td data-iv-role="cell">RFP26 239 Bridge and Pier Maintenance Program 2026</td>
<td data-iv-role="cell"><ul><li>Bridge construction and repair service</li></ul></td>
<td data-iv-role="cell">Request for Proposal (BPS)</td>
<td data-iv-role="cell">2026-06-09 3:17:41 PM</td>
<td data-iv-role="cell">2026-07-02 2:00:00 PM</td>
<td data-iv-role="cell"></td><td data-iv-role="cell">0</td>
<td data-iv-role="cell">2026-06-09 3:17:41 PM</td>
<td data-iv-role="cell">District of West Vancouver</td>
<td data-iv-role="cell"></td><td data-iv-role="cell"></td>
</tr>
"""


def test_parse_grid_row() -> None:
    row = BeautifulSoup(SAMPLE_ROW_HTML, "html.parser").find("tr")
    parsed = parse_grid_row(row)
    assert parsed is not None, "parse_grid_row returned None"
    assert parsed["tender_id"] == "230554"
    assert "Bridge and Pier" in parsed["title"]
    assert parsed["organization"] == "District of West Vancouver"
    print("parse_grid_row OK:", parsed["title"][:60])


def test_live_fetch() -> None:
    from scraper.bcbid_browser import bootstrap_bcbid_session

    session = create_bcbid_session()
    try:
        bootstrap_bcbid_session(session)
    except RuntimeError as exc:
        print(f"live bootstrap failed: {exc}")
        return

    response = bcbid_get(session, BCBID_BROWSE_URL)
    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    print(f"live GET: HTTP {response.status_code} url={response.url} title={title!r}")

    if is_browser_check(soup):
        print("live GET blocked by browser check (expected from datacenter/script clients)")
        return

    grid = soup.find("table", id="body_x_grid_grd")
    rows = grid.find_all("tr", attrs={"data-id": True}) if grid else []
    parsed = [parse_grid_row(row) for row in rows]
    parsed = [row for row in parsed if row]
    print(f"live parse OK: {len(parsed)} rows on page 1")
    if parsed:
        print(" sample:", parsed[0]["title"][:70])


if __name__ == "__main__":
    test_parse_grid_row()
    test_live_fetch()
