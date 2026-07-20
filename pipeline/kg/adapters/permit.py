"""Permit ingestion Observation adapter (Phase 1 dual-write)."""

from __future__ import annotations

import hashlib
from typing import Any

from pipeline.kg.adapters.base import ObservationAdapter
from pipeline.kg.constants import (
    ADAPTER_VERSION_PERMIT_V1,
    ENTITY_TYPE_PERMIT,
    SCHEMA_VERSION_V1,
)
from pipeline.kg.domain import ObservationDraft
from pipeline.kg.hashing import canonical_json

# Fields mirrored from permit import row dicts (includes resolution metadata when present).
_PERMIT_PAYLOAD_FIELDS = (
    "address",
    "permit_type",
    "project_value",
    "applicant",
    "normalized_applicant",
    "applicant_normalization_status",
    "architect",
    "issue_date",
    "application_date",
    "description",
    "contractor",
    "local_area",
    "source",
    "city",
    "external_id",
    "source_status_raw",
    "company_id",
    "canonical_merge_confidence",
    "canonical_merge_method",
)


def _permit_fingerprint_parts(
    row: dict[str, str], *, source: str
) -> tuple[str, str, str, str]:
    return (
        source,
        (row.get("address") or "").strip(),
        (row.get("project_value") or "").strip(),
        (row.get("applicant") or "").strip(),
    )


def derive_permit_external_id(row: dict[str, str], *, source: str) -> str:
    """Stable external_id for keyed and fingerprint-only permit rows."""
    external_id = (row.get("external_id") or "").strip()
    if external_id:
        return external_id[:200]

    fingerprint = _permit_fingerprint_parts(row, source=source)
    if not fingerprint[1] or not fingerprint[2]:
        raise ValueError(
            "permit row lacks address and project_value for fingerprint external_id"
        )

    digest = hashlib.sha256(
        canonical_json(list(fingerprint)).encode("utf-8")
    ).hexdigest()[:32]
    return f"fp:{digest}"


def build_permit_payload(row: dict[str, str], *, source: str) -> dict[str, Any]:
    """Canonical observation payload for a permit import row."""
    payload: dict[str, Any] = {
        field: (
            row.get(field, "")
            if field not in {"company_id", "canonical_merge_confidence"}
            else row.get(field)
        )
        for field in _PERMIT_PAYLOAD_FIELDS
    }
    payload.setdefault("source", source)
    payload["observation_kind"] = "permit_import_row"
    payload["derived_external_id"] = derive_permit_external_id(row, source=source)
    return payload


class PermitObservationAdapter(ObservationAdapter):
    """Dual-write adapter for db.permit_import upsert rows."""

    entity_type = ENTITY_TYPE_PERMIT
    adapter_version = ADAPTER_VERSION_PERMIT_V1
    schema_version = SCHEMA_VERSION_V1

    def source_key(self, raw: Any, **context: Any) -> str:
        source = context.get("source") or (
            raw.get("source") if isinstance(raw, dict) else ""
        )
        return str(source or "")

    def to_drafts(self, raw: Any, **context: Any) -> list[ObservationDraft]:
        if not isinstance(raw, dict):
            return []

        source = str(context.get("source") or raw.get("source") or "").strip()
        if not source:
            return []

        try:
            external_id = derive_permit_external_id(raw, source=source)
        except ValueError:
            return []

        payload = build_permit_payload(raw, source=source)
        return [
            ObservationDraft(
                source=source,
                external_id=external_id,
                payload=payload,
                entity_type=self.entity_type,
                schema_version=self.schema_version,
                adapter_version=self.adapter_version,
            )
        ]


def dual_write_permit_observations(
    session, rows: list[dict[str, str]], *, source: str
) -> None:
    """Best-effort dual-write hook for permit import — never raises to callers."""
    adapter = PermitObservationAdapter()
    adapter.dual_write_batch(session, rows, commit=True, source=source)
