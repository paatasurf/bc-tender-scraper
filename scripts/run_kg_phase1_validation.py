#!/usr/bin/env python3
"""Phase 1 staging validation — Observation spine sign-off checklist.

LOCAL DATABASE ONLY. Refuses production DATABASE_URL.

Usage:
  python scripts/run_kg_phase1_validation.py
  python scripts/run_kg_phase1_validation.py --dual-write-smoke
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import func, inspect, select, text


def _require_local_database_url() -> str:
    import config.env  # noqa: F401 — loads .env / .env.local

    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        raise SystemExit("DATABASE_URL not set — configure local Postgres only")
    lowered = database_url.lower()
    if any(token in lowered for token in ("railway", "rlwy.net", "production")):
        raise SystemExit("Refusing validation against production DATABASE_URL")
    return database_url


def _check(name: str, ok: bool, detail: str = "") -> dict:
    return {"check": name, "ok": ok, "detail": detail}


def run_validation(*, dual_write_smoke: bool = False) -> dict:
    _require_local_database_url()

    import config.env  # noqa: F401
    from db.connection import init_db, get_session_factory
    from db.models import KgObservation, KgOutboxEvent
    from pipeline.kg.adapters.permit import PermitObservationAdapter
    from pipeline.kg.constants import OUTBOX_EVENT_OBSERVATION_RECORDED
    from pipeline.kg.domain import ObservationDraft
    from pipeline.kg.flags import dual_write_enabled
    from pipeline.kg.store import record_observation

    init_ok = init_db(raise_on_failure=False)
    checks: list[dict] = []
    checks.append(_check("init_db", init_ok, "schema including migration 025"))

    if not init_ok:
        return {
            "phase": "P1",
            "validated_at": datetime.now(timezone.utc).isoformat(),
            "all_ok": False,
            "checks": checks,
            "error": "Database unavailable — start local Postgres or run API staging gate script",
        }

    factory = get_session_factory()
    session = factory()
    try:
        engine = session.get_bind()
        tables = set(inspect(engine).get_table_names())
        checks.append(_check("kg_observations_table", "kg_observations" in tables))
        checks.append(_check("kg_outbox_events_table", "kg_outbox_events" in tables))

        obs_before = session.scalar(select(func.count()).select_from(KgObservation)) or 0
        outbox_before = session.scalar(select(func.count()).select_from(KgOutboxEvent)) or 0

        checks.append(
            _check(
                "dual_write_flag_default_off",
                not dual_write_enabled(),
                "KG_OBSERVATION_DUAL_WRITE unset should default False",
            )
        )

        if dual_write_smoke:
            os.environ["KG_OBSERVATION_DUAL_WRITE"] = "1"
            draft = ObservationDraft(
                source="phase1_validation",
                external_id=f"smoke-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
                payload={"validation": True, "phase": 1},
                entity_type="permit",
            )
            result = record_observation(session, draft)
            session.commit()

            outbox = session.scalar(
                select(KgOutboxEvent).where(KgOutboxEvent.aggregate_id == result.observation_id)
            )
            checks.append(_check("record_observation_smoke", result.created or result.idempotent_replay))
            checks.append(
                _check(
                    "outbox_observation_recorded",
                    outbox is not None and outbox.event_type == OUTBOX_EVENT_OBSERVATION_RECORDED,
                )
            )

            adapter_row = {
                "external_id": "P1-VAL-1",
                "address": "1 Validation Way",
                "project_value": "1000",
                "applicant": "Validation Builder Ltd",
                "source": "vancouver",
                "city": "Vancouver",
            }
            stats = PermitObservationAdapter().dual_write_batch(
                session,
                [adapter_row],
                commit=True,
                source="vancouver",
            )
            checks.append(_check("permit_adapter_dual_write", stats.recorded >= 1 or stats.idempotent >= 1))

        obs_after = session.scalar(select(func.count()).select_from(KgObservation)) or 0
        outbox_after = session.scalar(select(func.count()).select_from(KgOutboxEvent)) or 0

        checks.append(
            _check(
                "observation_count_readable",
                True,
                f"observations before={obs_before} after={obs_after}",
            )
        )
        checks.append(
            _check(
                "outbox_count_readable",
                True,
                f"outbox before={outbox_before} after={outbox_after}",
            )
        )

        session.execute(text("SELECT 1"))
        checks.append(_check("session_alive", True))
    finally:
        session.close()

    all_ok = all(c["ok"] for c in checks if c["check"] not in {"observation_count_readable", "outbox_count_readable"})
    return {
        "phase": "P1",
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "all_ok": all_ok,
        "checks": checks,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="KG Phase 1 staging validation")
    parser.add_argument(
        "--dual-write-smoke",
        action="store_true",
        help="Enable KG_OBSERVATION_DUAL_WRITE=1 and insert one smoke observation",
    )
    args = parser.parse_args()
    report = run_validation(dual_write_smoke=args.dual_write_smoke)
    print(json.dumps(report, indent=2))
    if not report["all_ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
