from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from scraper.config import ARCH_TENDERS_CSV, COMMERCIAL_TENDERS_CSV, OUTPUT_CSV


@dataclass(frozen=True)
class CsvArtifact:
    name: str
    path: Path
    required: bool = True


TENDER_CSV_ARTIFACTS: tuple[CsvArtifact, ...] = (
    CsvArtifact("federal_merx_tenders", Path(OUTPUT_CSV)),
    CsvArtifact("architecture_tenders", Path(ARCH_TENDERS_CSV)),
    CsvArtifact("commercial_tenders", Path(COMMERCIAL_TENDERS_CSV)),
)


class CsvVerificationError(RuntimeError):
    pass


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _count_csv_rows(path: Path) -> int:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return sum(1 for _ in reader)


def verify_tender_csvs(*, not_before: datetime | None = None) -> dict[str, int | str]:
    """Verify tender CSV files exist, are readable, and were written after scrape start."""
    results: dict[str, int | str] = {}
    errors: list[str] = []

    for artifact in TENDER_CSV_ARTIFACTS:
        path = artifact.path
        if not path.exists():
            if artifact.required:
                errors.append(f"{artifact.name}: missing file {path}")
            continue

        stat = path.stat()
        if stat.st_size == 0:
            errors.append(f"{artifact.name}: empty file {path}")
            continue

        try:
            row_count = _count_csv_rows(path)
        except csv.Error as exc:
            errors.append(f"{artifact.name}: unreadable CSV ({exc})")
            continue

        if row_count <= 0:
            errors.append(f"{artifact.name}: no data rows in {path}")
            continue

        if not_before is not None:
            mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            if mtime < not_before:
                errors.append(
                    f"{artifact.name}: {path} mtime {mtime.isoformat()} "
                    f"is before scrape start {not_before.isoformat()}"
                )

        results[artifact.name] = row_count
        results[f"{artifact.name}_path"] = str(path.resolve())

    if errors:
        raise CsvVerificationError("; ".join(errors))

    print(f"[Pipeline] CSV verification passed: {results}")
    return results
