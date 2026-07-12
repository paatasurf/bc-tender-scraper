"""Append-only Observation store with transactional outbox skeleton."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.models import KgObservation, KgOutboxEvent
from pipeline.kg.constants import (
    OBSERVATION_STATUS_ACTIVE,
    OBSERVATION_STATUS_SUPERSEDED,
    OUTBOX_EVENT_OBSERVATION_RECORDED,
    OUTBOX_STATUS_PENDING,
)
from pipeline.kg.domain import ObservationDraft, RecordObservationResult, utc_now
from pipeline.kg.hashing import content_hash_for_payload

logger = logging.getLogger(__name__)


def _resolve_observed_at(draft: ObservationDraft) -> datetime:
    if draft.observed_at is not None:
        if draft.observed_at.tzinfo is None:
            return draft.observed_at.replace(tzinfo=timezone.utc)
        return draft.observed_at
    return utc_now()


def _enqueue_outbox(session: Session, *, observation_id: int, draft: ObservationDraft) -> None:
    session.add(
        KgOutboxEvent(
            event_type=OUTBOX_EVENT_OBSERVATION_RECORDED,
            aggregate_type="observation",
            aggregate_id=observation_id,
            payload={
                "source": draft.source,
                "external_id": draft.external_id,
                "entity_type": draft.entity_type,
                "adapter_version": draft.adapter_version,
            },
            status=OUTBOX_STATUS_PENDING,
        )
    )


def _find_active_observation_id(
    session: Session,
    *,
    source: str,
    external_id: str,
) -> int | None:
    return session.scalar(
        select(KgObservation.id).where(
            KgObservation.source == source,
            KgObservation.external_id == external_id,
            KgObservation.status == OBSERVATION_STATUS_ACTIVE,
        )
    )


def _find_idempotent_observation_id(
    session: Session,
    *,
    source: str,
    external_id: str,
    content_hash: str,
) -> int | None:
    return session.scalar(
        select(KgObservation.id).where(
            KgObservation.source == source,
            KgObservation.external_id == external_id,
            KgObservation.content_hash == content_hash,
        )
    )


def record_observation(
    session: Session,
    draft: ObservationDraft,
) -> RecordObservationResult:
    """Persist an observation append-only.

    Idempotent on (source, external_id, content_hash). When payload changes for the
    same external key, the prior active row is superseded and a new active row is
    inserted. raw_payload is never updated in place.
    """
    content_hash = content_hash_for_payload(draft.payload)
    observed_at = _resolve_observed_at(draft)

    existing_id = _find_idempotent_observation_id(
        session,
        source=draft.source,
        external_id=draft.external_id,
        content_hash=content_hash,
    )
    if existing_id is not None:
        return RecordObservationResult(
            observation_id=int(existing_id),
            created=False,
            idempotent_replay=True,
        )

    prior_active_id = _find_active_observation_id(
        session,
        source=draft.source,
        external_id=draft.external_id,
    )

    if prior_active_id is not None:
        session.execute(
            update(KgObservation)
            .where(KgObservation.id == prior_active_id)
            .values(status=OBSERVATION_STATUS_SUPERSEDED)
        )

    observation = KgObservation(
        source=draft.source,
        external_id=draft.external_id,
        content_hash=content_hash,
        observed_at=observed_at,
        status=OBSERVATION_STATUS_ACTIVE,
        raw_payload=dict(draft.payload),
        schema_version=draft.schema_version,
        adapter_version=draft.adapter_version,
        entity_type=draft.entity_type,
    )
    session.add(observation)
    session.flush()

    if prior_active_id is not None:
        session.execute(
            update(KgObservation)
            .where(KgObservation.id == prior_active_id)
            .values(superseded_by_id=observation.id)
        )

    _enqueue_outbox(session, observation_id=int(observation.id), draft=draft)
    session.flush()

    return RecordObservationResult(
        observation_id=int(observation.id),
        created=True,
        superseded_prior=prior_active_id is not None,
    )
