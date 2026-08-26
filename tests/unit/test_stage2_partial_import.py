"""Tests for Stage 2 of the partial-failure tender pipeline resilience
design: db/import_csv.py::import_all_csvs()'s new ``skip`` parameter.

This is the corruption-risk proof identified in the Stage 2 design audit:
if the Federal + MERX Open scrape didn't succeed this run, import_tenders()
must never be called at all -- not called with stale data, not called in
a "best effort" mode, never called. This test proves that skipping
"tenders" leaves an existing Federal Tender row's missing_from_source_count
and last_seen_at completely untouched, while MERX Architecture and
Commercial CSVs -- genuinely fresh, from independently-successful
scrapers -- still import normally in the same call.

Real-DB integration tests only, mirroring
tests/unit/test_federal_merx_open_commercial_freshness.py -- whether a
skipped import touches a row is genuine PostgreSQL ON CONFLICT DO UPDATE
behavior (or, for a skip, genuine *absence* of any SQL touching that
table at all) and cannot be meaningfully verified against a mock.
Skipped on CI and against any non-local DATABASE_URL.

Local Postgres in this environment may carry pre-existing committed
tenders/arch_tenders/commercial_tenders rows, so every test here uses a
unique url (via _uid()) and deletes only its own rows before starting --
never a blanket DELETE.
"""

from __future__ import annotations

import csv
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

import db.import_csv as import_csv_module
from db.import_csv import import_all_csvs
from db.models import ArchTender, CommercialTender, Tender

_FEDERAL_SOURCE = "buyandsell.gc.ca"


def _uid() -> str:
    return uuid.uuid4().hex[:12]


def _require_local_database_url() -> str:
    from tests.db_test_safety import _ci_skips_db_integration

    if _ci_skips_db_integration():
        pytest.skip(
            "DB integration tests skipped on CI (set CI_DATABASE_URL to enable)"
        )
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        pytest.skip("DATABASE_URL not configured")
    lowered = database_url.lower()
    if any(token in lowered for token in ("railway", "rlwy.net", "production")):
        pytest.skip(
            "Refusing Stage 2 partial-import integration tests against "
            "production DATABASE_URL"
        )
    return database_url


@pytest.fixture()
def local_db_session() -> Session:
    import config.env  # noqa: F401
    from db.connection import init_db

    _require_local_database_url()
    init_db()
    engine = create_engine(os.environ["DATABASE_URL"])
    factory = sessionmaker(bind=engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _cleanup(
    session: Session,
    *,
    tender_urls: list[str] | None = None,
    arch_urls: list[str] | None = None,
    commercial_urls: list[str] | None = None,
) -> None:
    if tender_urls:
        session.execute(
            text("DELETE FROM tenders WHERE url = ANY(:urls)"),
            {"urls": tender_urls},
        )
    if arch_urls:
        session.execute(
            text("DELETE FROM arch_tenders WHERE url = ANY(:urls)"),
            {"urls": arch_urls},
        )
    if commercial_urls:
        session.execute(
            text("DELETE FROM commercial_tenders WHERE url = ANY(:urls)"),
            {"urls": commercial_urls},
        )
    session.commit()


def test_skipping_tenders_leaves_federal_row_byte_identical_while_arch_and_commercial_import(
    local_db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The core Stage 2 corruption-risk proof, replaying the shape of the
    2026-08-19 incident: a pre-existing Federal Tender row (simulating a
    tender that has existed since a previous, successful day) must be
    completely untouched -- missing_from_source_count AND last_seen_at
    byte-identical -- when import_all_csvs(skip={"tenders"}) is called,
    even though fresh MERX Architecture and Commercial CSVs are present
    and import normally in the same call."""
    federal_url = f"https://example.test/federal/{_uid()}"
    arch_url = f"https://example.test/arch/{_uid()}"
    commercial_url = f"https://example.test/commercial/{_uid()}"

    _cleanup(local_db_session, tender_urls=[federal_url])
    try:
        # 1. Seed a pre-existing Federal row directly (simulating a
        # tender already imported on a previous, successful day) with a
        # known missing_from_source_count.
        local_db_session.add(
            Tender(
                title="Existing Federal Tender",
                url=federal_url,
                source=_FEDERAL_SOURCE,
                missing_from_source_count=3,
            )
        )
        local_db_session.commit()

        before = local_db_session.scalars(
            select(Tender).where(Tender.url == federal_url)
        ).one()
        before_scraped_at = before.scraped_at
        before_last_seen_at = before.last_seen_at
        before_missing_count = before.missing_from_source_count
        local_db_session.expunge(before)

        # 2. Fresh CSVs for the two independently-successful sources.
        arch_csv = tmp_path / "arch_tenders.csv"
        _write_csv(
            arch_csv,
            [
                "title",
                "company",
                "value",
                "deadline",
                "status",
                "category",
                "url",
                "tender_id",
            ],
            [
                {
                    "title": "Fresh Arch Tender",
                    "company": "",
                    "value": "",
                    "deadline": "",
                    "status": "Open",
                    "category": "",
                    "url": arch_url,
                    "tender_id": "",
                }
            ],
        )
        commercial_csv = tmp_path / "commercial_tenders.csv"
        _write_csv(
            commercial_csv,
            [
                "title",
                "company",
                "value",
                "deadline",
                "status",
                "category",
                "url",
                "tender_id",
                "source",
            ],
            [
                {
                    "title": "Fresh Commercial Tender",
                    "company": "",
                    "value": "",
                    "deadline": "",
                    "status": "Open",
                    "category": "",
                    "url": commercial_url,
                    "tender_id": "",
                    "source": "",
                }
            ],
        )

        # 3. Point every import_all_csvs() CSV path at either the fresh
        # temp files above or a guaranteed-nonexistent path -- "tenders"
        # is skipped so OUTPUT_CSV is never read regardless of what it
        # points at; the other non-tender sources (permits/reddit/news/
        # linkedin/jobs) gracefully no-op on a missing file (existing,
        # unchanged behavior -- see _read_csv()/import_permits()), so
        # pointing them at nonexistent paths keeps this test from
        # touching any real local dev CSV data.
        nonexistent = tmp_path / "does-not-exist.csv"
        monkeypatch.setattr(import_csv_module, "OUTPUT_CSV", str(nonexistent))
        monkeypatch.setattr(import_csv_module, "ARCH_TENDERS_CSV", str(arch_csv))
        monkeypatch.setattr(
            import_csv_module, "COMMERCIAL_TENDERS_CSV", str(commercial_csv)
        )
        monkeypatch.setattr(import_csv_module, "BUILDING_PERMITS_CSV", str(nonexistent))
        monkeypatch.setattr(import_csv_module, "REDDIT_SIGNALS_CSV", str(nonexistent))
        monkeypatch.setattr(import_csv_module, "NEWS_SIGNALS_CSV", str(nonexistent))
        monkeypatch.setattr(import_csv_module, "LINKEDIN_SIGNALS_CSV", str(nonexistent))
        monkeypatch.setattr(import_csv_module, "JOB_BANK_JOBS_CSV", str(nonexistent))

        # 4. The actual Stage 2 call under test.
        result = import_all_csvs(local_db_session, skip=frozenset({"tenders"}))

        # --- Proof: Federal/"tenders" was skipped, not attempted ---
        assert result["tenders"] == 0
        assert result["tenders_skipped"] is True

        after = local_db_session.scalars(
            select(Tender).where(Tender.url == federal_url)
        ).one()
        assert after.scraped_at == before_scraped_at
        assert after.last_seen_at == before_last_seen_at
        assert after.missing_from_source_count == before_missing_count

        # --- Proof: MERX Architecture and Commercial imported normally,
        # independent of the Federal skip ---
        assert result["arch_tenders"] == 1
        assert "arch_tenders_skipped" not in result
        assert result["commercial_tenders"] == 1
        assert "commercial_tenders_skipped" not in result

        arch_row = local_db_session.scalars(
            select(ArchTender).where(ArchTender.url == arch_url)
        ).one()
        assert arch_row.last_seen_at is not None

        commercial_row = local_db_session.scalars(
            select(CommercialTender).where(CommercialTender.url == commercial_url)
        ).one()
        assert commercial_row.last_seen_at is not None
    finally:
        _cleanup(
            local_db_session,
            tender_urls=[federal_url],
            arch_urls=[arch_url],
            commercial_urls=[commercial_url],
        )


def test_no_skip_imports_everything_as_before(
    local_db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No-regression proof: import_all_csvs() with skip=frozenset()
    (the default) imports every source exactly as before Stage 2 -- no
    ``*_skipped`` keys appear anywhere in the result."""
    tender_url = f"https://example.test/federal/{_uid()}"

    tender_csv = tmp_path / "tenders.csv"
    _write_csv(
        tender_csv,
        [
            "title",
            "organization",
            "category",
            "posted_date",
            "closing_date",
            "estimated_value",
            "location",
            "tender_id",
            "url",
            "source",
        ],
        [
            {
                "title": "Fresh Federal Tender",
                "organization": "",
                "category": "",
                "posted_date": "",
                "closing_date": "",
                "estimated_value": "",
                "location": "",
                "tender_id": "",
                "url": tender_url,
                "source": _FEDERAL_SOURCE,
            }
        ],
    )
    nonexistent = tmp_path / "does-not-exist.csv"

    monkeypatch.setattr(import_csv_module, "OUTPUT_CSV", str(tender_csv))
    monkeypatch.setattr(import_csv_module, "ARCH_TENDERS_CSV", str(nonexistent))
    monkeypatch.setattr(import_csv_module, "COMMERCIAL_TENDERS_CSV", str(nonexistent))
    monkeypatch.setattr(import_csv_module, "BUILDING_PERMITS_CSV", str(nonexistent))
    monkeypatch.setattr(import_csv_module, "REDDIT_SIGNALS_CSV", str(nonexistent))
    monkeypatch.setattr(import_csv_module, "NEWS_SIGNALS_CSV", str(nonexistent))
    monkeypatch.setattr(import_csv_module, "LINKEDIN_SIGNALS_CSV", str(nonexistent))
    monkeypatch.setattr(import_csv_module, "JOB_BANK_JOBS_CSV", str(nonexistent))

    try:
        result = import_all_csvs(local_db_session)  # skip defaults to frozenset()

        assert result["tenders"] == 1
        assert not any(key.endswith("_skipped") for key in result)
    finally:
        _cleanup(local_db_session, tender_urls=[tender_url])
