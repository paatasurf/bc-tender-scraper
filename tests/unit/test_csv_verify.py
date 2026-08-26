"""Pure unit tests for pipeline/csv_verify.py's Stage 2 ``skip`` parameter.

No DB, no real project CSV paths -- TENDER_CSV_ARTIFACTS is monkeypatched
to point at tmp_path files for the duration of each test, since its real
CsvArtifact.path values are baked in at module-import time from
scraper.config and can't be redirected by patching that config module
after the fact.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import pipeline.csv_verify as csv_verify_module
from pipeline.csv_verify import CsvArtifact, CsvVerificationError, verify_tender_csvs


def _write(path: Path, rows: str) -> None:
    path.write_text(rows, encoding="utf-8")


@pytest.fixture()
def three_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    federal = tmp_path / "tenders.csv"
    arch = tmp_path / "arch_tenders.csv"
    commercial = tmp_path / "commercial_tenders.csv"
    artifacts = (
        CsvArtifact("federal_merx_tenders", federal),
        CsvArtifact("architecture_tenders", arch),
        CsvArtifact("commercial_tenders", commercial),
    )
    monkeypatch.setattr(csv_verify_module, "TENDER_CSV_ARTIFACTS", artifacts)
    return federal, arch, commercial


def test_skipped_artifact_is_never_checked(three_artifacts, tmp_path: Path) -> None:
    federal, arch, commercial = three_artifacts
    # federal_merx_tenders is deliberately left missing/stale -- this
    # would normally fail verification, but it's in `skip`.
    _write(arch, "url\nhttps://example.test/a\n")
    _write(commercial, "url\nhttps://example.test/c\n")

    results = verify_tender_csvs(skip=frozenset({"federal_merx_tenders"}))

    assert results["federal_merx_tenders_skipped"] is True
    assert "federal_merx_tenders" not in results
    assert results["architecture_tenders"] == 1
    assert results["commercial_tenders"] == 1


def test_not_before_still_strict_for_non_skipped_success_artifact(
    three_artifacts, tmp_path: Path
) -> None:
    """Proof: skipping one artifact must never weaken the not_before
    staleness check for an artifact that is NOT in skip -- a genuinely
    stale, non-skipped artifact must still raise."""
    federal, arch, commercial = three_artifacts
    _write(arch, "url\nhttps://example.test/a\n")
    _write(commercial, "url\nhttps://example.test/c\n")

    from datetime import datetime, timedelta, timezone

    future_not_before = datetime.now(timezone.utc) + timedelta(hours=1)

    with pytest.raises(CsvVerificationError, match="architecture_tenders"):
        verify_tender_csvs(
            not_before=future_not_before,
            skip=frozenset({"federal_merx_tenders"}),
        )


def test_default_skip_checks_everything_strictly(three_artifacts) -> None:
    """No caller-provided skip -- reproduces the exact pre-Stage-2
    "check every artifact" behavior."""
    federal, arch, commercial = three_artifacts
    _write(federal, "url\nhttps://example.test/f\n")
    _write(arch, "url\nhttps://example.test/a\n")
    # commercial_tenders is missing -- must still raise with the default
    # empty skip set.

    with pytest.raises(CsvVerificationError, match="commercial_tenders"):
        verify_tender_csvs()
