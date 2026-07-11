"""Observation adapter framework (Phase 1 — dual-write only)."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Iterable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pipeline.kg.domain import DualWriteStats, ObservationDraft, RecordObservationResult
from pipeline.kg.flags import dual_write_enabled
from pipeline.kg.store import record_observation

logger = logging.getLogger(__name__)


class ObservationAdapter(ABC):
    """Convert an existing ETL record into one or more ObservationDraft rows."""

    entity_type: str
    adapter_version: str
    schema_version: str = "1"

    @abstractmethod
    def source_key(self, raw: Any, **context: Any) -> str:
        """Return the observation source namespace (e.g. permit city source)."""

    @abstractmethod
    def to_drafts(self, raw: Any, **context: Any) -> list[ObservationDraft]:
        """Map a single inbound record to zero or more observation drafts."""

    def dual_write_batch(
        self,
        session: Session,
        records: Iterable[Any],
        *,
        commit: bool = True,
        **context: Any,
    ) -> DualWriteStats:
        """Dual-write observations for a batch without affecting caller error semantics."""
        stats = DualWriteStats()
        if not dual_write_enabled():
            records_list = list(records)
            return DualWriteStats(skipped=len(records_list))

        for raw in records:
            try:
                drafts = self.to_drafts(raw, **context)
            except Exception:
                logger.exception(
                    "%s adapter failed to build draft",
                    self.__class__.__name__,
                )
                stats.errors += 1
                continue

            if not drafts:
                stats.skipped += 1
                continue

            for draft in drafts:
                try:
                    with session.begin_nested():
                        result = record_observation(session, draft)
                    stats.merge(result)
                except IntegrityError:
                    replay = self._idempotent_replay(session, draft)
                    if replay is not None:
                        stats.merge(replay)
                    else:
                        logger.exception(
                            "%s adapter integrity error source=%s external_id=%s",
                            self.__class__.__name__,
                            draft.source,
                            draft.external_id,
                        )
                        stats.errors += 1
                except Exception:
                    logger.exception(
                        "%s adapter failed to record observation source=%s external_id=%s",
                        self.__class__.__name__,
                        draft.source,
                        draft.external_id,
                    )
                    stats.errors += 1

        if commit and (stats.recorded or stats.idempotent or stats.superseded):
            try:
                session.commit()
            except Exception:
                logger.exception("%s adapter failed to commit observations", self.__class__.__name__)
                session.rollback()

        return stats

    @staticmethod
    def _idempotent_replay(session: Session, draft: ObservationDraft) -> RecordObservationResult | None:
        from pipeline.kg.hashing import content_hash_for_payload
        from pipeline.kg.store import _find_idempotent_observation_id

        content_hash = content_hash_for_payload(draft.payload)
        replay_id = _find_idempotent_observation_id(
            session,
            source=draft.source,
            external_id=draft.external_id,
            content_hash=content_hash,
        )
        if replay_id is None:
            return None
        return RecordObservationResult(
            observation_id=int(replay_id),
            created=False,
            idempotent_replay=True,
        )
