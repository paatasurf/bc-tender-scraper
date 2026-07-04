"""Script safety classification (Classes A–D). See scripts/CLASSIFICATION.md."""

from __future__ import annotations

from enum import Enum


class SafetyClass(str, Enum):
    """Nominal / effective script risk class."""

    A = "A"  # No write (no DB, or read-only SELECT)
    B = "B"  # Local data write (backfill, cache, staging)
    C = "C"  # Registry write (dry-run + apply workflow)
    D = "D"  # Schema DDL (init_db, migrations, ALTER)

    @property
    def label(self) -> str:
        return {
            SafetyClass.A: "Class A (No Write)",
            SafetyClass.B: "Class B (Local Write)",
            SafetyClass.C: "Class C (Registry Write)",
            SafetyClass.D: "Class D (Schema DDL)",
        }[self]

    @property
    def rank(self) -> int:
        return {"A": 0, "B": 1, "C": 2, "D": 3}[self.value]
