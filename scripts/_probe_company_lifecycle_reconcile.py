"""Reconcile company lifecycle distribution across signal layers (read-only)."""
from __future__ import annotations


from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collections import defaultdict
from datetime import date

from sqlalchemy import func, select, text

from db.connection import get_engine
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


def _classify(last: date | None) -> str:
    if last is None:
        return "no_observable_activity"
    days = (REF - last).days
    if days <= 365:
        return "active"
    if days <= 730:
        return "quiet"
    return "dormant"


def _print_distribution(label: str, buckets: dict[str, int], total: int) -> None:
    print(f"\n=== {label} ===")
    for key in ("active", "quiet", "dormant", "no_observable_activity"):
        count = buckets.get(key, 0)
        pct = count / total * 100 if total else 0
        print(f"  {key}: {count:,} ({pct:.1f}%)")


def _bucket_all(company_ids: list[int], last_by: dict[int, date], total: int) -> dict[str, int]:
    buckets: dict[str, int] = defaultdict(int)
    for company_id in company_ids:
        buckets[_classify(last_by.get(company_id))] += 1
    return dict(buckets)


def main() -> None:
    guard_readonly_db(_SCRIPT)
    engine = get_engine()
    with engine.connect() as conn:
        total = conn.execute(text("SELECT COUNT(*) FROM companies")).scalar() or 0
        company_ids = [int(row[0]) for row in conn.execute(text("SELECT id FROM companies")).all()]
        print(f"Reference date: {REF.isoformat()}")
        print(f"Total companies: {total:,}")

        # --- Permit linkage volume ---
        print("\n=== Permit linkage volume ===")
        permit_rows = conn.execute(text("SELECT COUNT(*) FROM permits WHERE applicant <> ''")).scalar()
        exact_match_rows = conn.execute(
            text(
                """
                SELECT COUNT(*)
                FROM permits p
                INNER JOIN companies c ON p.applicant = c.name
                WHERE p.applicant <> ''
                """
            )
        ).scalar()
        exact_match_companies = conn.execute(
            text(
                """
                SELECT COUNT(DISTINCT c.id)
                FROM companies c
                INNER JOIN permits p ON p.applicant = c.name
                WHERE p.applicant <> ''
                """
            )
        ).scalar()
        distinct_applicants = conn.execute(
            text(
                """
                SELECT COUNT(DISTINCT applicant)
                FROM permits
                WHERE applicant <> ''
                """
            )
        ).scalar()
        print(f"  permits with applicant: {permit_rows:,}")
        print(f"  permit rows exact-matched to company.name: {exact_match_rows:,}")
        print(f"  DISTINCT companies via exact applicant=c.name: {exact_match_companies:,}")
        print(f"  DISTINCT permit applicants: {distinct_applicants:,}")
        print(f"  avg permits per matched company: {exact_match_rows / exact_match_companies:.1f}")

    # Python layers needing ORM / normalize_vendor_name
    from db.connection import get_session

    with get_session() as session:
        indexes = build_company_indexes(session)
        last_fk_only: dict[int, date] = {}
        last_row_dates: dict[int, date] = {}
        last_exact_permit: dict[int, date] = {}
        last_normalized_permit: dict[int, date] = {}
        last_investigation: dict[int, date] = {}

        def bump(store: dict[int, date], cid: int, d: date | None) -> None:
            if d is None:
                return
            prev = store.get(cid)
            if prev is None or d > prev:
                store[cid] = d

        # Layer A: FK only (awards + outcomes)
        for cid, max_date in session.execute(
            select(ContractAward.company_id, func.max(ContractAward.award_date))
            .where(ContractAward.company_id.is_not(None))
            .where(ContractAward.award_date != "")
            .group_by(ContractAward.company_id)
        ).all():
            bump(last_fk_only, int(cid), _parse_iso(max_date))

        from db.models import TenderOutcome

        for cid, max_recorded in session.execute(
            select(TenderOutcome.company_id, func.max(TenderOutcome.recorded_at)).group_by(
                TenderOutcome.company_id
            )
        ).all():
            bump(last_fk_only, int(cid), max_recorded.date() if max_recorded else None)

        # Layer B: company row aggregates only
        for cid, lpd, lad in session.execute(
            select(Company.id, Company.last_project_date, Company.last_award_date)
        ).all():
            bump(last_row_dates, int(cid), _parse_iso(lpd))
            bump(last_row_dates, int(cid), _parse_iso(lad))

        # Layer C: exact permit applicant = company.name
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
            bump(last_exact_permit, int(cid), _parse_iso(max_issue))
            bump(last_exact_permit, int(cid), _parse_iso(max_app))

        # Layer D: normalized permit applicant (investigation combined)
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
            if not key:
                continue
            cid = indexes.normalized.get(key)
            if cid is None:
                continue
            bump(last_normalized_permit, int(cid), ref)

        # Layer E: investigation combined = row dates + award FK + normalized permits
        for cid in company_ids:
            for store in (last_row_dates, last_fk_only, last_normalized_permit):
                if cid in store:
                    bump(last_investigation, cid, store[cid])
            # award FK already in last_fk_only; row dates include last_award_date duplicate of FK partially

        # Rebuild investigation properly (same as probe script)
        last_investigation = {}
        for cid, lpd, lad in session.execute(
            select(Company.id, Company.last_project_date, Company.last_award_date)
        ).all():
            bump(last_investigation, int(cid), _parse_iso(lpd))
            bump(last_investigation, int(cid), _parse_iso(lad))
        for cid, max_date in session.execute(
            select(ContractAward.company_id, func.max(ContractAward.award_date))
            .where(ContractAward.company_id.is_not(None))
            .group_by(ContractAward.company_id)
        ).all():
            bump(last_investigation, int(cid), _parse_iso(max_date))
        for applicant, ref in applicant_max.items():
            key = normalize_vendor_name(applicant)
            cid = indexes.normalized.get(key) if key else None
            if cid is not None:
                bump(last_investigation, int(cid), ref)

        # Layer F: row dates + exact permit only (no normalize)
        last_row_plus_exact: dict[int, date] = {}
        for cid in company_ids:
            for store in (last_row_dates, last_exact_permit):
                if cid in store:
                    bump(last_row_plus_exact, cid, store[cid])

        # Layer G: row dates + award FK (no permits)
        last_row_plus_award_fk: dict[int, date] = {}
        for cid in company_ids:
            bump(last_row_plus_award_fk, cid, last_row_dates.get(cid))
            bump(last_row_plus_award_fk, cid, last_fk_only.get(cid))

        # Coverage stats
        print("\n=== Companies with ANY activity signal ===")
        layers = [
            ("FK only (awards+outcomes)", last_fk_only),
            ("Row dates (last_project_date + last_award_date)", last_row_dates),
            ("Exact permit applicant=c.name", last_exact_permit),
            ("Normalized permit applicant", last_normalized_permit),
            ("Investigation combined", last_investigation),
            ("Row dates + exact permit", last_row_plus_exact),
            ("Row dates + award FK", last_row_plus_award_fk),
        ]
        for name, store in layers:
            linked = len(store)
            print(f"  {name}: {linked:,} ({linked / total * 100:.1f}%)")

        _print_distribution(
            "A) FK-only — matches implemented resolver",
            _bucket_all(company_ids, last_fk_only, total),
            total,
        )
        _print_distribution(
            "B) Company row dates only (last_project_date + last_award_date)",
            _bucket_all(company_ids, last_row_dates, total),
            total,
        )
        _print_distribution(
            "C) Exact permit applicant=c.name only",
            _bucket_all(company_ids, last_exact_permit, total),
            total,
        )
        _print_distribution(
            "D) Investigation combined (row dates + award FK + normalized permits)",
            _bucket_all(company_ids, last_investigation, total),
            total,
        )
        _print_distribution(
            "E) Row dates + exact permit (no normalize, no award FK re-scan)",
            _bucket_all(company_ids, last_row_plus_exact, total),
            total,
        )

        # Incremental: what each signal adds beyond FK-only
        print("\n=== Incremental lift over FK-only (companies newly linked) ===")
        fk_set = set(last_fk_only)
        for name, store in layers[1:]:
            extra = set(store) - fk_set
            print(f"  {name}: +{len(extra):,} companies")

        # Extra probes for user-reported 5034/1414/2257/5429
        print("\n=== Extra probes ===")
        for row in session.execute(
            text(
                """
                SELECT COALESCE(NULLIF(company_lifecycle, ''), '(empty)') AS lc, COUNT(*) AS n
                FROM companies
                GROUP BY 1
                ORDER BY n DESC
                """
            )
        ).all():
            print(f"  company_lifecycle column {row.lc}: {row.n:,}")

        active_permit_companies = session.scalar(
            text(
                """
                SELECT COUNT(DISTINCT c.id)
                FROM companies c
                JOIN permits p ON p.applicant = c.name
                WHERE p.applicant <> '' AND p.is_active = true
                """
            )
        )
        active_permit_rows = session.scalar(
            text(
                """
                SELECT COUNT(*)
                FROM permits p
                JOIN companies c ON p.applicant = c.name
                WHERE p.applicant <> '' AND p.is_active = true
                """
            )
        )
        print(f"  companies with is_active=true permit (exact name): {active_permit_companies:,}")
        print(f"  is_active=true permit rows matched: {active_permit_rows:,}")

        # Normalized permit only + award FK (already computed in quick probe)
        norm_only = {}
        for applicant, ref in applicant_max.items():
            key = normalize_vendor_name(applicant)
            cid = indexes.normalized.get(key) if key else None
            if cid is not None:
                bump(norm_only, int(cid), ref)
        award_norm = dict(last_fk_only)
        for cid in company_ids:
            bump(award_norm, cid, norm_only.get(cid))
        _print_distribution(
            "F) Award FK + normalized permit (NO row dates)",
            _bucket_all(company_ids, award_norm, total),
            total,
        )
        _print_distribution(
            "G) Normalized permit ONLY (NO row dates, NO awards)",
            _bucket_all(company_ids, norm_only, total),
            total,
        )


if __name__ == "__main__":
    main()
