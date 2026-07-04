"""Check if 'any signal active' explains 5034/1414/2257/5429 hybrid."""
from __future__ import annotations


from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collections import defaultdict
from datetime import date

from sqlalchemy import func, select, text

from db.connection import get_session
from db.db_safety import guard_readonly_db
_SCRIPT = Path(__file__).name
from db.models import Company, ContractAward, Permit
from pipeline.company_matching import build_company_indexes, normalize_vendor_name

REF = date(2026, 7, 2)


def _parse_iso(raw: str | None) -> date | None:
    if not raw:
        return None
    text_value = str(raw).strip().replace("/", "-")[:10]
    try:
        return date.fromisoformat(text_value)
    except ValueError:
        return None


def _bucket(last: date | None) -> str:
    if last is None:
        return "no_observable_activity"
    days = (REF - last).days
    if days <= 365:
        return "active"
    if days <= 730:
        return "quiet"
    return "dormant"


def main() -> None:
    guard_readonly_db(_SCRIPT)
    with get_session() as session:
        company_ids = [int(row[0]) for row in session.execute(text("SELECT id FROM companies")).all()]
        indexes = build_company_indexes(session)

        last_by_signal: dict[str, dict[int, date]] = {
            "row_lpd": {},
            "row_lad": {},
            "award_fk": {},
            "norm_permit": {},
            "exact_permit": {},
        }

        for cid, lpd, lad in session.execute(
            select(Company.id, Company.last_project_date, Company.last_award_date)
        ).all():
            d = _parse_iso(lpd)
            if d:
                last_by_signal["row_lpd"][int(cid)] = d
            d = _parse_iso(lad)
            if d:
                last_by_signal["row_lad"][int(cid)] = d

        for cid, max_date in session.execute(
            select(ContractAward.company_id, func.max(ContractAward.award_date))
            .where(ContractAward.company_id.is_not(None))
            .where(ContractAward.award_date != "")
            .group_by(ContractAward.company_id)
        ).all():
            d = _parse_iso(max_date)
            if d:
                last_by_signal["award_fk"][int(cid)] = d

        applicant_max: dict[str, date] = {}
        for applicant, issue, app_date in session.execute(
            select(Permit.applicant, Permit.issue_date, Permit.application_date).where(
                Permit.applicant != ""
            )
        ).all():
            ref = _parse_iso(issue) or _parse_iso(app_date)
            if ref is None:
                continue
            prev = applicant_max.get(applicant)
            if prev is None or ref > prev:
                applicant_max[applicant] = ref

        for applicant, ref in applicant_max.items():
            key = normalize_vendor_name(applicant)
            cid = indexes.normalized.get(key) if key else None
            if cid is not None:
                last_by_signal["norm_permit"][int(cid)] = max(
                    last_by_signal["norm_permit"].get(int(cid), date.min),
                    ref,
                )

        for cid, max_issue, max_app in session.execute(
            text(
                """
                SELECT c.id,
                       MAX(NULLIF(p.issue_date, '')),
                       MAX(NULLIF(p.application_date, ''))
                FROM companies c
                INNER JOIN permits p ON p.applicant = c.name
                WHERE p.applicant <> ''
                GROUP BY c.id
                """
            )
        ).all():
            for raw in (max_issue, max_app):
                d = _parse_iso(raw)
                if d:
                    last_by_signal["exact_permit"][int(cid)] = max(
                        last_by_signal["exact_permit"].get(int(cid), date.min),
                        d,
                    )

        # MAX across all signals (investigation style)
        max_all: dict[int, date] = {}
        for cid in company_ids:
            dates = [
                store[cid]
                for store in last_by_signal.values()
                if cid in store
            ]
            if dates:
                max_all[cid] = max(dates)

        # ANY-signal optimistic: best bucket among signals
        optimistic: dict[str, int] = defaultdict(int)
        for cid in company_ids:
            buckets = [_bucket(store.get(cid)) for store in last_by_signal.values()]
            if all(b == "no_observable_activity" for b in buckets):
                optimistic["no_observable_activity"] += 1
            elif "active" in buckets:
                optimistic["active"] += 1
            elif "quiet" in buckets:
                optimistic["quiet"] += 1
            else:
                optimistic["dormant"] += 1

        max_buckets: dict[str, int] = defaultdict(int)
        for cid in company_ids:
            max_buckets[_bucket(max_all.get(cid))] += 1

        print("MAX-date across row_lpd, row_lad, award_fk, norm_permit, exact_permit:")
        for k in ("active", "quiet", "dormant", "no_observable_activity"):
            print(f"  {k}: {max_buckets[k]:,}")

        print("\nOptimistic ANY-signal (active if ANY signal active):")
        for k in ("active", "quiet", "dormant", "no_observable_activity"):
            print(f"  {k}: {optimistic[k]:,}")

        # FK + exact permit without row dates (already ~2967 active)
        fk_exact: dict[int, date] = {}
        for cid in company_ids:
            dates = []
            if cid in last_by_signal["award_fk"]:
                dates.append(last_by_signal["award_fk"][cid])
            if cid in last_by_signal["exact_permit"]:
                dates.append(last_by_signal["exact_permit"][cid])
            if dates:
                fk_exact[cid] = max(dates)
        fk_exact_buckets: dict[str, int] = defaultdict(int)
        for cid in company_ids:
            fk_exact_buckets[_bucket(fk_exact.get(cid))] += 1
        print("\nAward FK + exact permit (no row dates):")
        for k in ("active", "quiet", "dormant", "no_observable_activity"):
            print(f"  {k}: {fk_exact_buckets[k]:,}")


if __name__ == "__main__":
    main()
