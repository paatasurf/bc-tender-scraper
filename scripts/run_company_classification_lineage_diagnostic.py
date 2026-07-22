#!/usr/bin/env python3
"""Company classification lineage diagnostic (Class A -- no write).

Read-only, single-company companion to
``run_company_classification_audit.py`` (PR-MARKET-2B) and
``run_company_classification_evidence_review.py`` (PR-MARKET-2C/2C1):
traces exactly why ONE named company ended up with its current
``company_type`` and whether it currently clears the construction Market
GC/Trade-Contractor cohort's subject-independent eligibility checks. See
``pipeline.company_classification_lineage_diagnostic`` for the full
contract.

Never accepts or trusts a UI-supplied numeric profile id as
``Company.id`` -- the only input is ``--identity``, an exact (never
fuzzy) string compared against ``Company.name``/``Company.display_name``.
Zero or more than one match fails closed (``not_found``/``ambiguous``);
this script never guesses which row was meant. There is no
``--company-id`` flag and never will be.

The standard artifact printed/written by this script is aggregate-only:
resolution status, candidate count, review category, small closed-
vocabulary counts, boolean cohort-eligibility checks, and a digest
sensitive to the full evidence but never revealing it.

Raw evidence (company id, name, display name, entity_role, company_type,
confidence_score, primary_trade, dominant_sector, CIP fields,
classification method/category, KNOWN_FIRMS/CLASSIFICATION_RULES matches,
conflict signals, cohort-check results, and rule provenance) is ONLY ever
printed -- never written to the artifact, a file, or any log -- and ONLY
when ``--show-evidence`` is passed AND this process is running in a real,
attended terminal (both stdin and stdout must be actual, unmocked TTYs;
piped, redirected, CI, or mocked streams are refused). For that reason,
``--show-evidence`` refuses to run at all when combined with
``--artifact-path`` -- there is deliberately no way to make this script
write raw evidence anywhere.

This script has no ``--apply`` and no way to write to any database under
any flag combination -- it never assigns or changes ``company_type``,
``KNOWN_FIRMS``, cohort logic, scoring, or any Company column, and never
calls AI, enrichment, the scraper, or the scheduler.

Single connection, single explicit transaction: ``SET TRANSACTION
ISOLATION LEVEL REPEATABLE READ, READ ONLY`` is issued as the very first
statement on that connection, before anything else runs on it, and the
transaction is always rolled back (success or failure) before this
process exits.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config.env  # noqa: F401,E402  (loads .env before importing db.connection)

from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from db.connection import get_engine  # noqa: E402
from db.db_safety import guard_readonly_db_from_args  # noqa: E402
from db.merge_dry_run_provenance import get_git_commit_sha  # noqa: E402
from pipeline.company_classification_lineage_diagnostic import (  # noqa: E402
    ARTIFACT_SCHEMA_VERSION,
    LineageEvidence,
    build_lineage_diagnostic,
)

_SCRIPT = Path(__file__).name
DEFAULT_ARTIFACT_PATH = (
    ROOT / "exports" / "company_classification_lineage_diagnostic_class_a.json"
)
TRANSACTION_MODE = "REPEATABLE READ, READ ONLY"


def _is_attended_terminal() -> bool:
    """True only when BOTH stdin and stdout are the process's real,
    unmocked interactive TTYs -- never a pipe, redirect, CI runner, or
    mocked stream. Checked independently of any production-write
    authorization (this script never writes to any database) -- this
    only ever gates whether raw evidence may be displayed."""
    for stream, real_stream in (
        (sys.stdin, sys.__stdin__),
        (sys.stdout, sys.__stdout__),
    ):
        if stream is not real_stream:
            return False
        try:
            fd = stream.fileno()
        except (AttributeError, OSError, ValueError, io.UnsupportedOperation):
            return False
        if not os.isatty(fd):
            return False
        if hasattr(stream, "isatty") and not stream.isatty():
            return False
    return True


def run_diagnostic(
    engine: Engine,
    *,
    identity: str,
) -> tuple[dict[str, Any], LineageEvidence | None]:
    """Run the whole diagnostic inside one connection and one explicit,
    always-rolled-back, ``REPEATABLE READ, READ ONLY`` transaction.
    Raises whatever ``build_lineage_diagnostic`` raises."""
    conn = engine.connect()
    trans = conn.begin()
    session: Session | None = None
    try:
        conn.execute(text(f"SET TRANSACTION ISOLATION LEVEL {TRANSACTION_MODE}"))
        session = Session(bind=conn)
        return build_lineage_diagnostic(session, identity=identity)
    finally:
        if session is not None:
            session.close()
        trans.rollback()
        conn.close()


def _print_evidence(evidence: LineageEvidence | None) -> None:
    """Prints directly to this process's real stdout only -- never via
    the logging module (which could be configured to also sink to a
    file), and never returned or retained by the caller."""
    print("\n" + "!" * 70)
    print("  ATTENDED-TERMINAL-ONLY EVIDENCE -- do not redirect, pipe, or")
    print("  paste this output anywhere it could be persisted (files, chat")
    print("  logs, tickets). It is not written to any artifact, file, or")
    print("  application log by this script.")
    print("!" * 70)
    if evidence is None:
        print("\nNo evidence -- resolution did not resolve to exactly one company.")
        return
    print(f"\ncompany_id={evidence.company_id}")
    print(f"company_name={evidence.company_name!r}")
    print(f"display_name={evidence.display_name!r}")
    print(f"entity_role={evidence.entity_role!r}")
    print(f"company_type={evidence.company_type!r}")
    print(f"confidence_score={evidence.confidence_score!r}")
    print(f"primary_trade={evidence.primary_trade!r}")
    print(f"dominant_sector={evidence.dominant_sector!r}")
    print(f"cip_company_type={evidence.cip_company_type!r}")
    print(f"cip_entity_class={evidence.cip_entity_class!r}")
    print(f"cip_primary_trade={evidence.cip_primary_trade!r}")
    print(f"classification_method={evidence.classification_method!r}")
    print(
        f"classification_internal_category={evidence.classification_internal_category!r}"
    )
    print(f"classification_market_category={evidence.classification_market_category!r}")
    print(f"classification_confidence={evidence.classification_confidence!r}")
    print(f"known_firms_match_category={evidence.known_firms_match_category!r}")
    print(f"matching_rule_categories={list(evidence.matching_rule_categories)}")
    print(f"review_category={evidence.review_category!r}")
    print(f"conflict_signals={list(evidence.conflict_signals)}")
    print(f"passes_entity_analytics_filter={evidence.passes_entity_analytics_filter}")
    print(f"passes_person_name_filter={evidence.passes_person_name_filter}")
    print(
        f"passes_gc_cohort_isolation_allowlist={evidence.passes_gc_cohort_isolation_allowlist!r}"
    )
    print("\nprovenance:")
    for line in evidence.provenance:
        print(f"  - {line}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--identity",
        required=True,
        help=(
            "Exact company name to resolve (compared literally against "
            "Company.name and Company.display_name -- never fuzzy, never "
            "normalized). This is the ONLY way to target a company; a "
            "numeric profile id from any UI is never accepted here."
        ),
    )
    parser.add_argument(
        "--use-production",
        action="store_true",
        help=(
            "Read DATABASE_URL_PRODUCTION instead of local DATABASE_URL "
            "(Class A read-only; banner only -- this script never writes)."
        ),
    )
    parser.add_argument(
        "--artifact-path",
        type=Path,
        default=None,
        help=(
            f"Output artifact JSON path (default: {DEFAULT_ARTIFACT_PATH} "
            "unless --show-evidence is given). Cannot be combined with "
            "--show-evidence."
        ),
    )
    parser.add_argument(
        "--show-evidence",
        action="store_true",
        help=(
            "Print raw evidence for the resolved company (id, name, "
            "display name, classification fields, CIP fields, rule "
            "matches, conflict signals, cohort-check results, "
            "provenance) to THIS terminal only. Requires a real, "
            "attended TTY on both stdin and stdout. Never written to the "
            "artifact, a file, or any log. Cannot be combined with "
            "--artifact-path."
        ),
    )
    args = parser.parse_args()

    if args.show_evidence and args.artifact_path is not None:
        parser.error(
            "--show-evidence cannot be combined with --artifact-path -- "
            "raw evidence must never be written to a file."
        )

    if args.show_evidence and not _is_attended_terminal():
        print(
            "[lineage-diagnostic] Refusing --show-evidence: requires a real, "
            "attended terminal (TTY) on both stdin and stdout. Piped, "
            "redirected, CI, or mocked streams are not accepted.",
            file=sys.stderr,
        )
        return 1

    artifact_path = args.artifact_path
    if artifact_path is None and not args.show_evidence:
        artifact_path = DEFAULT_ARTIFACT_PATH

    guard_readonly_db_from_args(args, script_name=_SCRIPT)

    engine = get_engine()
    try:
        aggregate, evidence = run_diagnostic(engine, identity=args.identity)
    finally:
        engine.dispose()

    artifact = {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "git_commit_sha": get_git_commit_sha(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "transaction_mode": TRANSACTION_MODE,
        **aggregate,
    }

    if artifact_path is not None:
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")

    print(json.dumps(artifact, indent=2))
    if artifact_path is not None:
        print(f"\nWrote {artifact_path}")

    if args.show_evidence:
        _print_evidence(evidence)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
