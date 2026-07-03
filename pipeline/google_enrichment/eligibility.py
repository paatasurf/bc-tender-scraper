"""Select companies eligible for Google enrichment."""

from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from db.models import Company
from pipeline.google_enrichment.config import GoogleEnrichmentSettings
from pipeline.google_enrichment.constants import enriched, error, no_match, pending, review, stale


def mark_stale_companies(session: Session, settings: GoogleEnrichmentSettings) -> int:
    result = session.execute(
        text(
            """
            UPDATE companies
            SET google_enrichment_status = :stale_status
            WHERE lifecycle_status = 'active'
              AND is_operating = true
              AND google_enrichment_status = :enriched_status
              AND google_last_updated IS NOT NULL
              AND google_last_updated < NOW() - make_interval(days => :stale_days)
            """
        ),
        {
            "stale_status": stale,
            "enriched_status": enriched,
            "stale_days": settings.stale_days,
        },
    )
    session.commit()
    return int(result.rowcount or 0)


def fetch_eligible_companies(
    session: Session,
    settings: GoogleEnrichmentSettings,
    *,
    batch_size: int | None = None,
    company_ids: list[int] | None = None,
) -> list[Company]:
    limit = batch_size or settings.batch_size
    if company_ids:
        rows = session.execute(
            text(
                """
                SELECT id
                FROM companies
                WHERE id = ANY(:company_ids)
                  AND lifecycle_status = 'active'
                  AND is_operating = true
                  AND google_enrichment_status <> :review_status
                ORDER BY total_value DESC NULLS LAST, id ASC
                LIMIT :batch_size
                """
            ),
            {
                "company_ids": company_ids,
                "review_status": review,
                "batch_size": limit,
            },
        ).all()
        ids = [int(row.id) for row in rows]
        if not ids:
            return []
        return list(session.scalars(select(Company).where(Company.id.in_(ids))).all())

    rows = session.execute(
        text(
            """
            SELECT id
            FROM companies c
            WHERE c.lifecycle_status = 'active'
              AND c.is_operating = true
              AND c.google_enrichment_status NOT IN (:review_status)
              AND (
                    c.google_place_id IS NULL
                    OR c.google_enrichment_status IN (:pending_status, :error_status)
                    OR (
                        c.google_enrichment_status IN (:enriched_status, :stale_status)
                        AND c.google_last_updated < NOW() - make_interval(days => :stale_days)
                    )
                    OR (
                        c.google_enrichment_status = :no_match_status
                        AND c.google_last_updated < NOW() - make_interval(days => :no_match_days)
                    )
              )
            ORDER BY
              CASE
                WHEN c.google_enrichment_status = :stale_status THEN 0
                WHEN c.google_place_id IS NULL THEN 1
                ELSE 2
              END,
              c.total_value DESC NULLS LAST,
              c.id ASC
            LIMIT :batch_size
            """
        ),
        {
            "review_status": review,
            "pending_status": pending,
            "error_status": error,
            "enriched_status": enriched,
            "stale_status": stale,
            "no_match_status": no_match,
            "stale_days": settings.stale_days,
            "no_match_days": settings.no_match_retry_days,
            "batch_size": limit,
        },
    ).all()
    ids = [int(row.id) for row in rows]
    if not ids:
        return []
    return list(session.scalars(select(Company).where(Company.id.in_(ids))).all())


def fetch_reserved_place_ids(session: Session, *, exclude_company_id: int) -> frozenset[str]:
    rows = session.execute(
        text(
            """
            SELECT google_place_id
            FROM companies
            WHERE google_place_id IS NOT NULL
              AND google_place_id <> ''
              AND id <> :company_id
            """
        ),
        {"company_id": exclude_company_id},
    ).all()
    return frozenset(str(row.google_place_id) for row in rows if row.google_place_id)
