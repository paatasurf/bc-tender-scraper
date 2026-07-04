"""Read-only platform capability audit probes (production)."""
from __future__ import annotations


from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import json
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from sqlalchemy import text

from db.connection import get_engine
from db.db_safety import guard_readonly_db
_SCRIPT = Path(__file__).name

BASE = "https://bc-tender-scraper-production.up.railway.app"
COMPANY_ID = 8638


def fetch_json(path: str, timeout: int = 180) -> dict:
    url = f"{BASE}{path}"
    req = Request(url, headers={"Accept": "application/json"})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def section(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def main() -> None:
    guard_readonly_db(_SCRIPT)
    now = datetime.now(timezone.utc)
    print(f"AUDIT ref UTC: {now.isoformat()}")

    # 1. Unified opportunities
    section("1. TENDER MATCHING — unified opportunities")
    unified = fetch_json(f"/api/companies/{COMPANY_ID}/opportunities/unified?limit=10")
    print("total", unified.get("total"), "model_coverage", unified.get("model_coverage"))
    items = unified.get("items") or []
    ids = [int(it["tender_id"]) for it in items]
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, is_open, lifecycle_status, closing_at
                FROM tenders WHERE id = ANY(:ids)
                ORDER BY id
                """
            ),
            {"ids": ids},
        ).all()
    row_map = {r.id: r for r in rows}
    for i, it in enumerate(items, 1):
        tid = int(it["tender_id"])
        db = row_map.get(tid)
        ch = it.get("construction_hybrid")
        bp = it.get("business_pursuit")
        print(f"\n  #{i} tender_id={tid} model={it.get('model')}")
        print(f"     DB: is_open={db.is_open if db else '?'} lifecycle={db.lifecycle_status if db else '?'} closing_at={db.closing_at if db else '?'}")
        if ch:
            print(f"     CH: score={ch.get('score')} label={ch.get('score_label')} reasons={len(ch.get('reasons') or [])}")
        if bp:
            print(f"     BP: score={bp.get('score')} verdict={bp.get('pursuit_verdict')} reasons={len(bp.get('reasons') or [])}")

    sample = items[0] if items else {}
    print("\n  Sample item keys:", sorted(sample.keys()))
    if ch := sample.get("construction_hybrid"):
        print("  CH keys:", sorted(ch.keys()))
    if bp := sample.get("business_pursuit"):
        print("  BP keys:", sorted(bp.keys()))

    # 2. Competitive intelligence
    section("2. COMPETITIVE INTELLIGENCE")
    try:
        ci = fetch_json(f"/api/companies/id/{COMPANY_ID}/competitive-intelligence?peer_limit=5")
    except HTTPError as exc:
        print("HTTP error", exc.code, exc.read()[:500])
        ci = {}
    print("top-level keys:", sorted(ci.keys()) if ci else [])
    peers = ci.get("peers") or ci.get("peer_companies") or []
    if not peers and isinstance(ci.get("competitive_profile"), dict):
        peers = ci["competitive_profile"].get("peers") or []
    print("peer count:", len(peers))
    peer_ids = []
    for p in peers[:5]:
        pid = p.get("company_id") or p.get("id")
        name = p.get("name") or p.get("company_name")
        threat = p.get("threat_score") or p.get("score")
        peer_ids.append(int(pid)) if pid else None
        print(f"  peer: id={pid} name={name!r} threat/score={threat}")

    if peer_ids:
        with get_engine().connect() as conn:
            lc = conn.execute(
                text(
                    """
                    SELECT id, name, lifecycle_status, is_operating, last_activity_at
                    FROM companies WHERE id = ANY(:ids)
                    """
                ),
                {"ids": peer_ids},
            ).all()
        print("\n  Peer lifecycle_status from DB:")
        for r in lc:
            print(f"    {r.id} {r.name[:40]!r} status={r.lifecycle_status} operating={r.is_operating}")

    threats = ci.get("threat_rankings") or ci.get("threats") or []
    print("threat block count:", len(threats))
    if isinstance(ci.get("summary"), dict):
        print("summary keys:", sorted(ci["summary"].keys()))

    # 3. Contractor reliability sample
    section("3. CONTRACTOR RELIABILITY — top-5 by permit count")
    with get_engine().connect() as conn:
        top5 = conn.execute(
            text(
                """
                SELECT c.id, c.name, COUNT(p.id) AS permit_count
                FROM companies c
                JOIN permits p ON p.applicant = c.name AND p.applicant <> ''
                GROUP BY c.id, c.name
                ORDER BY permit_count DESC
                LIMIT 5
                """
            )
        ).all()
        for row in top5:
            stats = conn.execute(
                text(
                    """
                    SELECT
                      COUNT(*) FILTER (WHERE p.is_active = true) AS active_n,
                      COUNT(*) FILTER (WHERE p.is_active = false) AS inactive_n,
                      COUNT(*) FILTER (WHERE p.lifecycle_status = 'stale') AS stale_n,
                      COUNT(*) FILTER (WHERE p.lifecycle_status = 'active') AS active_status_n,
                      COUNT(*) FILTER (WHERE p.lifecycle_status = 'completed') AS completed_n,
                      COUNT(*) FILTER (WHERE p.source_status_raw <> '') AS has_source_status,
                      MIN(p.issue_date) FILTER (WHERE p.is_active = true AND p.issue_date ~ '^[0-9]{4}-') AS oldest_active_issue,
                      MAX(p.issue_date) FILTER (WHERE p.is_active = true AND p.issue_date ~ '^[0-9]{4}-') AS newest_active_issue
                    FROM permits p
                    WHERE p.applicant = :name AND p.applicant <> ''
                    """
                ),
                {"name": row.name},
            ).one()
            print(f"\n  {row.name} (id={row.id}, permits={row.permit_count})")
            print(f"    is_active true/false: {stats.active_n}/{stats.inactive_n}")
            print(f"    lifecycle active/stale/completed: {stats.active_status_n}/{stats.stale_n}/{stats.completed_n}")
            print(f"    source_status_raw populated: {stats.has_source_status}")
            print(f"    oldest/newest active issue_date: {stats.oldest_active_issue} / {stats.newest_active_issue}")

        permit_totals = conn.execute(
            text(
                """
                SELECT lifecycle_status, is_active, COUNT(*)
                FROM permits GROUP BY 1,2 ORDER BY 3 DESC
                """
            )
        ).all()
        print("\n  Global permit lifecycle distribution:")
        for r in permit_totals:
            print(f"    {r.lifecycle_status} is_active={r.is_active}: {r.count:,}")

    # 4. Early signals
    section("4. EARLY SIGNALS")
    es = fetch_json(
        f"/api/early-signals?company_id={COMPANY_ID}&limit=10&min_score=0&lookback_days=90"
    )
    print("keys:", sorted(es.keys()))
    signals = es.get("signals") or es.get("items") or []
    print("signal count:", len(signals))
    permit_ids = []
    for s in signals[:10]:
        sid = s.get("id") or s.get("permit_id")
        stype = s.get("signal_type")
        score = s.get("score")
        permit_ids.append(int(sid)) if sid and stype == "permit_application" else None
        print(f"  id={sid} type={stype} score={score} city={s.get('city')}")
    permit_ids = [int(s.get("id")) for s in signals if s.get("signal_type") == "permit_application" and s.get("id")]
    if permit_ids:
        with get_engine().connect() as conn:
            chk = conn.execute(
                text(
                    """
                    SELECT id, is_active, lifecycle_status, issue_date, application_date
                    FROM permits WHERE id = ANY(:ids)
                    """
                ),
                {"ids": permit_ids},
            ).all()
        print("\n  Linked permit lifecycle in DB:")
        for r in chk:
            print(f"    permit {r.id}: is_active={r.is_active} status={r.lifecycle_status} issue={r.issue_date}")

    # 5. Company profile
    section("5. COMPANY INTELLIGENCE — profile 8638")
    profile = fetch_json(f"/api/companies/id/{COMPANY_ID}")
    print("keys:", sorted(profile.keys())[:30], "... total", len(profile.keys()))
    for field in (
        "id",
        "name",
        "lifecycle_status",
        "is_operating",
        "last_activity_at",
        "company_lifecycle",
        "total_projects",
        "award_count",
        "google_rating",
        "enrichment_status",
        "primary_trade",
        "data_sources",
    ):
        if field in profile:
            val = profile[field]
            if isinstance(val, str) and len(val) > 80:
                val = val[:80] + "..."
            print(f"  {field}: {val}")
    contacts = fetch_json(f"/api/companies/id/{COMPANY_ID}/contacts")
    print("\n  contacts keys:", sorted(contacts.keys()))
    clist = contacts.get("contacts") or contacts.get("items") or []
    print("  contacts count:", len(clist) if isinstance(clist, list) else contacts.get("total"))


if __name__ == "__main__":
    main()
