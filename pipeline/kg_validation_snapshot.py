"""Read-only KG validation snapshot for staging gate sign-off."""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from db.models import KgEngineDecisionRecord, KgObservation, KgOutboxEvent, Permit, PipelineRun
from pipeline.kg.flags import dual_write_enabled
from pipeline.registry_gateway.flags import gateway_enforce_enabled, gateway_shadow_enabled


def collect_kg_validation_snapshot(session: Session) -> dict[str, Any]:
    """Aggregate P1/P2 staging metrics without mutating data."""
    started = time.perf_counter()

    obs_by_source = dict(
        session.execute(
            select(KgObservation.source, func.count())
            .group_by(KgObservation.source)
            .order_by(KgObservation.source)
        ).all()
    )
    obs_total = sum(obs_by_source.values())

    outbox_pending = session.scalar(
        select(func.count())
        .select_from(KgOutboxEvent)
        .where(KgOutboxEvent.status == "pending")
    ) or 0
    outbox_total = session.scalar(select(func.count()).select_from(KgOutboxEvent)) or 0

    decisions_by_type = dict(
        session.execute(
            select(KgEngineDecisionRecord.decision, func.count())
            .group_by(KgEngineDecisionRecord.decision)
            .order_by(KgEngineDecisionRecord.decision)
        ).all()
    )
    decisions_total = sum(decisions_by_type.values())

    decisions_by_path = dict(
        session.execute(
            select(KgEngineDecisionRecord.source_path, func.count())
            .group_by(KgEngineDecisionRecord.source_path)
            .order_by(KgEngineDecisionRecord.source_path)
        ).all()
    )

    recent_decisions = [
        {
            "id": row.id,
            "decision": row.decision,
            "source_path": row.source_path,
            "trigger_source": row.trigger_source,
            "raw_identity": (row.raw_identity or "")[:120],
            "gateway_mode": row.gateway_mode,
            "legacy_proceeded": row.legacy_proceeded,
            "reject_reason": row.reject_reason,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in session.scalars(
            select(KgEngineDecisionRecord).order_by(KgEngineDecisionRecord.id.desc()).limit(20)
        ).all()
    ]

    unexpected = [
        d
        for d in recent_decisions
        if d["decision"] in {"reject", "review"}
        and d["gateway_mode"] == "enforce"
    ]

    permits_total = session.scalar(select(func.count()).select_from(Permit)) or 0

    recent_permit_run = session.scalar(
        select(PipelineRun)
        .where(PipelineRun.step.in_(["scrape-building-permits", "import-csvs"]))
        .order_by(PipelineRun.id.desc())
        .limit(1)
    )
    recent_award_run = session.scalar(
        select(PipelineRun)
        .where(PipelineRun.step.in_(["import-contract-awards", "populate-award-companies"]))
        .order_by(PipelineRun.id.desc())
        .limit(1)
    )

    tables_ok = True
    for table in ("kg_observations", "kg_outbox_events", "kg_engine_decision_records"):
        exists = session.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = :t)"
            ),
            {"t": table},
        )
        if not exists:
            tables_ok = False

    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)

    return {
        "schema_ok": tables_ok,
        "flags": {
            "KG_OBSERVATION_DUAL_WRITE": dual_write_enabled(),
            "KG_GATEWAY_SHADOW": gateway_shadow_enabled(),
            "KG_GATEWAY_ENFORCE": gateway_enforce_enabled(),
        },
        "observations": {
            "total": obs_total,
            "by_source": obs_by_source,
        },
        "outbox": {
            "total": outbox_total,
            "pending": outbox_pending,
        },
        "decisions": {
            "total": decisions_total,
            "by_decision": decisions_by_type,
            "by_source_path": decisions_by_path,
            "recent": recent_decisions,
            "unexpected_enforce_blocks": unexpected,
        },
        "permits_total": permits_total,
        "recent_pipeline_runs": {
            "permits": _pipeline_run_summary(recent_permit_run),
            "awards": _pipeline_run_summary(recent_award_run),
        },
        "snapshot_ms": elapsed_ms,
    }


def _pipeline_run_summary(record: PipelineRun | None) -> dict[str, Any] | None:
    if record is None:
        return None
    duration_ms: float | None = None
    if record.started_at and record.finished_at:
        duration_ms = round((record.finished_at - record.started_at).total_seconds() * 1000, 1)
    return {
        "id": record.id,
        "step": record.step,
        "status": record.status,
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "finished_at": record.finished_at.isoformat() if record.finished_at else None,
        "duration_ms": duration_ms,
        "counts_json": record.counts_json,
        "error": record.error or "",
    }
