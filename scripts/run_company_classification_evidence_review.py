#!/usr/bin/env python3
"""Company classification conflict evidence review (Class A -- no write).

Read-only companion to ``run_company_classification_audit.py``
(PR-MARKET-2B, merged): reuses that module's own aggregate result and its
exact conflict-determination predicates verbatim (see
``pipeline.company_classification_evidence_review``) to let a human
reviewer see WHICH ``confirmed_conflict`` companies were flagged and WHY,
before any classification fix is ever proposed or applied. This script
proposes, applies, or performs no classification change itself.

The standard artifact printed/written by this script is aggregate-only --
identical in spirit to the audit's own artifact, plus
``review_candidate_count`` and a ``review_digest`` that is sensitive to
each confirmed_conflict candidate's identity/type/trade/signals but never
reveals them.

Raw candidate details (company id, current company_type, primary_trade,
conflict signals, proposed category, rule provenance) are ONLY ever
printed -- never written to the artifact, a file, or any log -- and ONLY
when ``--show-candidates`` is passed AND this process is running in a
real, attended terminal (both stdin and stdout must be actual, unmocked
TTYs; piped, redirected, CI, or mocked streams are refused). For that
reason, ``--show-candidates`` refuses to run at all when combined with
``--artifact-path`` -- there is deliberately no way to make this script
write raw candidate details anywhere.

This script has no ``--apply`` and no way to write to any database under
any flag combination -- it never assigns or changes ``company_type``,
``primary_trade``, or any other Company column, and never calls AI,
enrichment, the scraper, or the scheduler.

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
from pipeline.company_classification_evidence_review import (  # noqa: E402
    ARTIFACT_SCHEMA_VERSION,
    ConflictCandidate,
    build_evidence_review,
)

_SCRIPT = Path(__file__).name
DEFAULT_ARTIFACT_PATH = (
    ROOT / "exports" / "company_classification_evidence_review_class_a.json"
)
TRANSACTION_MODE = "REPEATABLE READ, READ ONLY"


def _is_attended_terminal() -> bool:
    """True only when BOTH stdin and stdout are the process's real,
    unmocked interactive TTYs -- never a pipe, redirect, CI runner, or
    mocked stream. Checked independently of any production-write
    authorization (this script never writes to any database) -- this
    only ever gates whether raw candidate identity may be displayed."""
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


def run_review(
    engine: Engine,
    *,
    sample_size: int | None,
) -> tuple[dict[str, Any], list[ConflictCandidate]]:
    """Run the whole evidence review inside one connection and one
    explicit, always-rolled-back, ``REPEATABLE READ, READ ONLY``
    transaction. Raises whatever ``build_evidence_review`` raises."""
    conn = engine.connect()
    trans = conn.begin()
    session: Session | None = None
    try:
        conn.execute(text(f"SET TRANSACTION ISOLATION LEVEL {TRANSACTION_MODE}"))
        session = Session(bind=conn)
        return build_evidence_review(session, sample_size=sample_size)
    finally:
        if session is not None:
            session.close()
        trans.rollback()
        conn.close()


def _print_candidates(candidates: list[ConflictCandidate]) -> None:
    """Prints directly to this process's real stdout only -- never via
    the logging module (which could be configured to also sink to a
    file), and never returned or retained by the caller."""
    print("\n" + "!" * 70)
    print("  ATTENDED-TERMINAL-ONLY EVIDENCE -- do not redirect, pipe, or")
    print("  paste this output anywhere it could be persisted (files, chat")
    print("  logs, tickets). It is not written to any artifact, file, or")
    print("  application log by this script.")
    print("!" * 70)
    if not candidates:
        print("\nNo confirmed_conflict candidates.")
        return
    print(f"\n{len(candidates)} confirmed_conflict candidate(s):\n")
    for candidate in candidates:
        print(f"- company_id={candidate.company_id}")
        print(f"  company_type={candidate.company_type!r}")
        print(f"  primary_trade={candidate.primary_trade!r}")
        print(f"  signals={list(candidate.signals)}")
        print(f"  proposed_category={candidate.proposed_category!r}")
        for line in candidate.provenance:
            print(f"  - {line}")
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
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
            "unless --show-candidates is given). Cannot be combined with "
            "--show-candidates."
        ),
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=None,
        help=(
            "Examine at most this many GC/Trade-Contractor-tagged "
            "companies, deterministically ordered by Company.id ascending "
            "(SQL LIMIT). Default: all such companies."
        ),
    )
    parser.add_argument(
        "--show-candidates",
        action="store_true",
        help=(
            "Print confirmed_conflict candidate evidence (company id, "
            "company_type, primary_trade, signals, proposed category, "
            "rule provenance) to THIS terminal only. Requires a real, "
            "attended TTY on both stdin and stdout. Never written to the "
            "artifact, a file, or any log. Cannot be combined with "
            "--artifact-path."
        ),
    )
    args = parser.parse_args()

    if args.show_candidates and args.artifact_path is not None:
        parser.error(
            "--show-candidates cannot be combined with --artifact-path -- "
            "raw candidate details must never be written to a file."
        )

    if args.show_candidates and not _is_attended_terminal():
        print(
            "[evidence-review] Refusing --show-candidates: requires a real, "
            "attended terminal (TTY) on both stdin and stdout. Piped, "
            "redirected, CI, or mocked streams are not accepted.",
            file=sys.stderr,
        )
        return 1

    artifact_path = args.artifact_path
    if artifact_path is None and not args.show_candidates:
        artifact_path = DEFAULT_ARTIFACT_PATH

    guard_readonly_db_from_args(args, script_name=_SCRIPT)

    engine = get_engine()
    try:
        aggregate, candidates = run_review(engine, sample_size=args.sample_size)
    finally:
        engine.dispose()

    artifact = {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "git_commit_sha": get_git_commit_sha(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "transaction_mode": TRANSACTION_MODE,
        "sample_size": args.sample_size,
        **aggregate,
    }

    if artifact_path is not None:
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")

    print(json.dumps(artifact, indent=2))
    if artifact_path is not None:
        print(f"\nWrote {artifact_path}")

    if args.show_candidates:
        _print_candidates(candidates)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
