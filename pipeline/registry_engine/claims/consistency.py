"""Pure evaluation of Classification Claims cross-table consistency.

No database, no SQLAlchemy, no I/O -- this module only checks invariants
that already-fetched rows must satisfy. It exists because the schema's own
FK/CHECK constraints cannot express every invariant this ledger needs:

- a claim's ``primary_evidence_content_hash`` must actually match some
  ``claim_evidence`` row belonging to that claim (the schema has no way to
  enforce "this hash appears somewhere in a child table");
- a ``superseded`` event's ``related_claim_id`` must share
  (company_id, claim_type, predicate) with its own claim (a cross-row
  invariant, not expressible as a column constraint);
- a claim's or event's ``rule_set_version_id`` must be compatible with its
  own claim_type and be effective by its own timestamp (again cross-row).

Fed with a snapshot of the six tables' relevant columns, this module cannot
tell whether that snapshot is fresh or how it was obtained -- that is
``db.classification_claims_consistency_audit``'s job (the DB-facing layer
that fetches rows, calls this module, and adds ``dataset_hash``/
``schema_version``). Used by the Class A audit
(``scripts/run_claims_consistency_audit.py``) so a Gateway bug, a direct
manual SQL edit, or a future migration cannot silently drift out of these
invariants without being caught.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ClaimRow:
    claim_id: str
    company_id: int
    claim_type: str
    predicate: str
    source_type: str
    primary_evidence_content_hash: str
    idempotency_key: str
    rule_set_version_id: str
    effective_at: datetime


@dataclass(frozen=True)
class EvidenceRow:
    claim_evidence_id: str
    claim_id: str
    evidence_source: str
    content_hash: str


@dataclass(frozen=True)
class EventRow:
    event_id: str
    claim_id: str
    event_type: str
    related_claim_id: str | None
    rule_set_version_id: str
    event_at: datetime


@dataclass(frozen=True)
class RuleSetRow:
    rule_set_version_id: str
    claim_type: str
    effective_from: datetime


def _is_valid_hash(value: str) -> bool:
    return isinstance(value, str) and bool(_SHA256_RE.fullmatch(value))


def evaluate_claims_consistency(
    *,
    claims: Sequence[ClaimRow],
    evidence: Sequence[EvidenceRow],
    events: Sequence[EventRow],
    rule_sets: Sequence[RuleSetRow],
) -> dict:
    """Deterministic, side-effect-free consistency report over one snapshot
    of the four relevant tables. Returns ``{"status": "PASS"|"FAIL",
    "findings": [...], "counts": {...}}`` -- callers attach
    ``dataset_hash``/``schema_version`` themselves (see module docstring)."""
    findings: list[str] = []

    claims_by_id = {c.claim_id: c for c in claims}
    rule_sets_by_id = {r.rule_set_version_id: r for r in rule_sets}

    evidence_by_claim: dict[str, list[EvidenceRow]] = {}
    for ev in evidence:
        evidence_by_claim.setdefault(ev.claim_id, []).append(ev)
        if ev.claim_id not in claims_by_id:
            findings.append(
                f"claim_evidence {ev.claim_evidence_id}: dangling claim_id {ev.claim_id!r}"
            )
        if not _is_valid_hash(ev.content_hash):
            findings.append(
                f"claim_evidence {ev.claim_evidence_id}: invalid content_hash format "
                f"{ev.content_hash!r}"
            )

    for c in claims:
        if not _is_valid_hash(c.idempotency_key):
            findings.append(
                f"classification_claims {c.claim_id}: invalid idempotency_key format "
                f"{c.idempotency_key!r}"
            )
        if not _is_valid_hash(c.primary_evidence_content_hash):
            findings.append(
                f"classification_claims {c.claim_id}: invalid primary_evidence_content_hash "
                f"format {c.primary_evidence_content_hash!r}"
            )

        matching_evidence = [
            ev
            for ev in evidence_by_claim.get(c.claim_id, [])
            if ev.content_hash == c.primary_evidence_content_hash
        ]
        if not matching_evidence:
            findings.append(
                f"classification_claims {c.claim_id}: no claim_evidence row matches "
                "primary_evidence_content_hash"
            )

        rule_set = rule_sets_by_id.get(c.rule_set_version_id)
        if rule_set is None:
            findings.append(
                f"classification_claims {c.claim_id}: dangling rule_set_version_id "
                f"{c.rule_set_version_id!r}"
            )
        else:
            if rule_set.claim_type != c.claim_type:
                findings.append(
                    f"classification_claims {c.claim_id}: rule_set_version "
                    f"{c.rule_set_version_id!r} claim_type={rule_set.claim_type!r} does not "
                    f"match claim_type={c.claim_type!r}"
                )
            if rule_set.effective_from > c.effective_at:
                findings.append(
                    f"classification_claims {c.claim_id}: rule_set_version "
                    f"{c.rule_set_version_id!r} is not yet effective at this claim's "
                    "effective_at"
                )

    events_by_claim: dict[str, list[EventRow]] = {}
    for event in events:
        events_by_claim.setdefault(event.claim_id, []).append(event)
        parent = claims_by_id.get(event.claim_id)
        if parent is None:
            findings.append(
                f"claim_events {event.event_id}: dangling claim_id {event.claim_id!r}"
            )

        rule_set = rule_sets_by_id.get(event.rule_set_version_id)
        if rule_set is None:
            findings.append(
                f"claim_events {event.event_id}: dangling rule_set_version_id "
                f"{event.rule_set_version_id!r}"
            )
        elif parent is not None:
            if rule_set.claim_type != parent.claim_type:
                findings.append(
                    f"claim_events {event.event_id}: rule_set_version "
                    f"{event.rule_set_version_id!r} claim_type={rule_set.claim_type!r} does "
                    f"not match parent claim's claim_type={parent.claim_type!r}"
                )
            if rule_set.effective_from > event.event_at:
                findings.append(
                    f"claim_events {event.event_id}: rule_set_version "
                    f"{event.rule_set_version_id!r} is not yet effective at event_at"
                )

        if event.event_type == "superseded":
            if event.related_claim_id is None:
                findings.append(
                    f"claim_events {event.event_id}: superseded event missing related_claim_id"
                )
            else:
                related = claims_by_id.get(event.related_claim_id)
                if related is None:
                    findings.append(
                        f"claim_events {event.event_id}: dangling related_claim_id "
                        f"{event.related_claim_id!r}"
                    )
                elif parent is not None and (
                    related.company_id,
                    related.claim_type,
                    related.predicate,
                ) != (parent.company_id, parent.claim_type, parent.predicate):
                    findings.append(
                        f"claim_events {event.event_id}: related_claim_id "
                        f"{event.related_claim_id!r} is outside the company_id/claim_type/"
                        "predicate scope of its own claim"
                    )

    for claim_id, claim_events in events_by_claim.items():
        if len(claim_events) > 1:
            findings.append(
                f"classification_claims {claim_id}: has {len(claim_events)} terminal events "
                "(uq_claim_events_one_per_claim should make this impossible -- investigate "
                "as a possible constraint bypass)"
            )

    status = "PASS" if not findings else "FAIL"
    return {
        "status": status,
        "findings": findings,
        "counts": {
            "claims": len(claims),
            "evidence": len(evidence),
            "events": len(events),
            "rule_sets": len(rule_sets),
        },
    }
