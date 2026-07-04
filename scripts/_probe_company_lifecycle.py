"""Read-only Company Lifecycle Phase 1 probe — production aggregates."""
from __future__ import annotations


from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collections import defaultdict
from datetime import date, datetime, timezone

from sqlalchemy import func, select, text

from db.connection import get_session, init_db
from db.db_safety import guard_readonly_db
_SCRIPT = Path(__file__).name
from db.models import Company, ContractAward, Permit
from pipeline.company_matching import build_company_indexes, normalize_vendor_name

REF = date(2026, 7, 2)


def _parse_iso(raw: str | None) -> date | None:
    if not raw:
        return None
    s = str(raw).strip().replace("/", "-")[:10]
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _classify_last_activity(last: date | None) -> str:
    if last is None:
        return "no_activity"
    days = (REF - last).days
    if days < 0:
        return "active"
    if days <= 365:
        return "active"
    if days <= 730:
        return "quiet"
    return "dormant"


def main() -> None:
    guard_readonly_db(_SCRIPT)
    with get_session() as s:
        total_companies = s.scalar(select(func.count()).select_from(Company)) or 0
        print(f"=== companies total: {total_companies:,} ===\n")

        # --- company_lifecycle column today ---
        print("=== company_lifecycle column (current) ===")
        for row in s.execute(
            text(
                """
                SELECT COALESCE(NULLIF(company_lifecycle, ''), '(empty)') AS lc, COUNT(*) AS n
                FROM companies GROUP BY 1 ORDER BY n DESC
                """
            )
        ).all():
            print(f"  {row.lc}: {row.n:,}")

        # --- last_project_date / last_award_date coverage ---
        print("\n=== company row date fields ===")
        for label, sql in [
            ("last_project_date set", "SELECT COUNT(*) FROM companies WHERE last_project_date <> ''"),
            ("last_award_date set", "SELECT COUNT(*) FROM companies WHERE last_award_date <> ''"),
            ("either set", "SELECT COUNT(*) FROM companies WHERE last_project_date <> '' OR last_award_date <> ''"),
            ("neither set", "SELECT COUNT(*) FROM companies WHERE last_project_date = '' AND last_award_date = ''"),
        ]:
            print(f"  {label}: {s.scalar(text(sql)):,}")

        # --- contract_awards FK ---
        print("\n=== contract_awards company_id FK ===")
        aw = s.execute(
            text(
                """
                SELECT
                    COUNT(*) AS total_awards,
                    COUNT(*) FILTER (WHERE company_id IS NOT NULL) AS with_company_id,
                    COUNT(DISTINCT company_id) FILTER (WHERE company_id IS NOT NULL) AS distinct_companies
                FROM contract_awards
                """
            )
        ).one()
        print(f"  total awards: {aw.total_awards:,}")
        print(f"  with company_id: {aw.with_company_id:,}")
        print(f"  distinct companies linked: {aw.distinct_companies:,}")

        companies_with_award_fk = s.scalar(
            text("SELECT COUNT(DISTINCT company_id) FROM contract_awards WHERE company_id IS NOT NULL")
        )
        print(f"  companies with >=1 award FK: {companies_with_award_fk:,}")

        # --- permit applicant join quality ---
        print("\n=== permit applicant join (normalize_vendor_name) ===")
        indexes = build_company_indexes(s)
        distinct_applicants = s.execute(
            text(
                """
                SELECT DISTINCT applicant FROM permits
                WHERE applicant IS NOT NULL AND applicant <> ''
                """
            )
        ).all()
        matched_applicants = 0
        matched_company_ids: set[int] = set()
        for (applicant,) in distinct_applicants:
            key = normalize_vendor_name(applicant)
            if key and key in indexes.normalized:
                matched_applicants += 1
                matched_company_ids.add(indexes.normalized[key])

        print(f"  distinct permit applicants: {len(distinct_applicants):,}")
        print(f"  applicants matching a company (normalized): {matched_applicants:,}")
        print(
            f"  fraction of distinct applicants matched: {matched_applicants / len(distinct_applicants) * 100:.1f}%"
            if distinct_applicants
            else "  (no applicants)"
        )
        print(f"  companies matched via permit applicant: {len(matched_company_ids):,}")
        print(
            f"  fraction of all companies: {len(matched_company_ids) / total_companies * 100:.1f}%"
            if total_companies
            else ""
        )

        # Exact name match (populate_companies_from_permits style)
        exact_matched = s.scalar(
            text(
                """
                SELECT COUNT(DISTINCT c.id)
                FROM companies c
                INNER JOIN permits p ON p.applicant = c.name
                WHERE p.applicant <> ''
                """
            )
        )
        print(f"  companies with exact applicant=c.name match: {exact_matched:,}")

        # --- per-company most recent activity (multi-signal) ---
        print("\n=== building per-company last activity (permits normalized + awards FK + row dates) ===")
        last_by_company: dict[int, date] = {}

        def bump(cid: int, d: date | None) -> None:
            if d is None:
                return
            prev = last_by_company.get(cid)
            if prev is None or d > prev:
                last_by_company[cid] = d

        # Row-level aggregates already on company
        for cid, lpd, lad in s.execute(
            select(Company.id, Company.last_project_date, Company.last_award_date)
        ).all():
            bump(cid, _parse_iso(lpd))
            bump(cid, _parse_iso(lad))

        # Awards FK max date
        for cid, max_date in s.execute(
            select(ContractAward.company_id, func.max(ContractAward.award_date))
            .where(ContractAward.company_id.isnot(None))
            .group_by(ContractAward.company_id)
        ).all():
            bump(cid, _parse_iso(max_date))

        # Permits via normalized applicant (scan applicants grouped)
        applicant_max: dict[str, str] = {}
        for applicant, issue, app_date in s.execute(
            select(Permit.applicant, Permit.issue_date, Permit.application_date).where(
                Permit.applicant != ""
            )
        ).all():
            ref = _parse_iso(issue) or _parse_iso(app_date)
            if ref is None:
                continue
            iso = ref.isoformat()
            prev = applicant_max.get(applicant)
            if prev is None or iso > prev:
                applicant_max[applicant] = iso

        for applicant, iso in applicant_max.items():
            key = normalize_vendor_name(applicant)
            if not key:
                continue
            cid = indexes.normalized.get(key)
            if cid is None:
                continue
            bump(cid, _parse_iso(iso))

        bucket_counts: dict[str, int] = defaultdict(int)
        for cid in s.scalars(select(Company.id)).all():
            bucket_counts[_classify_last_activity(last_by_company.get(cid))] += 1

        print("  classification (12/24mo rules on combined last activity):")
        for k in ("active", "quiet", "dormant", "no_activity"):
            n = bucket_counts[k]
            pct = n / total_companies * 100 if total_companies else 0
            print(f"    {k}: {n:,} ({pct:.1f}%)")

        linked = total_companies - bucket_counts["no_activity"]
        print(f"  companies with any linkable activity: {linked:,} ({linked / total_companies * 100:.1f}%)")

        # Distribution using ONLY last_project_date on company row (current classifier input)
        print("\n=== classification using ONLY companies.last_project_date (current classifier) ===")
        lpd_buckets: dict[str, int] = defaultdict(int)
        for lpd in s.scalars(select(Company.last_project_date)):
            lpd_buckets[_classify_last_activity(_parse_iso(lpd))] += 1
        for k in ("active", "quiet", "dormant", "no_activity"):
            print(f"    {k}: {lpd_buckets[k]:,}")

        # Active permits only for match count
        print("\n=== permit join with is_active=true only ===")
        active_applicants = s.execute(
            text(
                """
                SELECT DISTINCT applicant FROM permits
                WHERE applicant <> '' AND is_active = true
                """
            )
        ).all()
        active_matched = sum(
            1
            for (a,) in active_applicants
            if normalize_vendor_name(a) in indexes.normalized
        )
        print(f"  distinct active permit applicants: {len(active_applicants):,}")
        print(f"  matched to companies: {active_matched:,}")

        print("\n=== data_sources on companies (no activity) ===")
        for row in s.execute(
            text(
                """
                SELECT
                    COUNT(*) FILTER (WHERE total_projects = 0 AND award_count = 0) AS no_stats,
                    COUNT(*) FILTER (WHERE cardinality(data_sources) = 0) AS empty_sources,
                    COUNT(*) FILTER (WHERE last_enriched_at IS NOT NULL) AS ever_enriched
                FROM companies
                """
            )
        ).one():
            print(f"  no_projects_and_no_awards: {row.no_stats:,}")
            print(f"  empty data_sources: {row.empty_sources:,}")
            print(f"  ever_enriched (google/ai): {row.ever_enriched:,}")


if __name__ == "__main__":
    main()
