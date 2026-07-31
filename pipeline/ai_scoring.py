from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

import anthropic
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from config.env import get_anthropic_api_key, get_env
from db.models import ArchTender, CommercialTender, Tender

CLAUDE_MODEL = "claude-sonnet-4-5"
SCORING_DELAY_SECONDS = 0.5
DEFAULT_AI_BATCH_LIMIT = 10
DEFAULT_ANTHROPIC_TIMEOUT_SECONDS = 30.0
DEFAULT_AI_SCORING_SYNC_TIME_BUDGET_SECONDS = 240.0
FEDERAL_GOV_SOURCE = "buyandsell.gc.ca"
MERX_SOURCE = "merx.com"

ScoredTender = ArchTender | CommercialTender | Tender
ScoredModel = type[ArchTender] | type[CommercialTender] | type[Tender]


@dataclass
class _ScoringRunBudget:
    limit_seconds: float | None
    started_at: float = field(default_factory=time.monotonic)
    exhausted: bool = False

    def remaining_seconds(self) -> float | None:
        if self.limit_seconds is None:
            return None
        return self.limit_seconds - (time.monotonic() - self.started_at)

    def can_start_request(self, reserve_seconds: float) -> bool:
        remaining = self.remaining_seconds()
        if remaining is None:
            return True
        if remaining >= reserve_seconds:
            return True
        self.exhausted = True
        return False

    def can_sleep(self, delay_seconds: float) -> bool:
        remaining = self.remaining_seconds()
        return remaining is None or remaining > delay_seconds


def _ai_batch_limit() -> int:
    raw = get_env("AI_SCORING_MAX_PER_RUN", str(DEFAULT_AI_BATCH_LIMIT))
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_AI_BATCH_LIMIT


def _anthropic_timeout_seconds() -> float:
    raw = get_env("ANTHROPIC_TIMEOUT_SECONDS", str(int(DEFAULT_ANTHROPIC_TIMEOUT_SECONDS)))
    try:
        return max(5.0, float(raw))
    except ValueError:
        return DEFAULT_ANTHROPIC_TIMEOUT_SECONDS


def ai_scoring_sync_time_budget_seconds() -> float:
    raw = get_env(
        "AI_SCORING_SYNC_TIME_BUDGET_SECONDS",
        str(int(DEFAULT_AI_SCORING_SYNC_TIME_BUDGET_SECONDS)),
    )
    try:
        return max(30.0, float(raw))
    except ValueError:
        return DEFAULT_AI_SCORING_SYNC_TIME_BUDGET_SECONDS


def _anthropic_client(api_key: str) -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=api_key, timeout=_anthropic_timeout_seconds())


def _value_is_empty(value: str | None) -> bool:
    if not value or not str(value).strip():
        return True
    digits = re.sub(r"[^\d.]", "", str(value))
    if not digits:
        return True
    try:
        return float(digits) == 0
    except ValueError:
        return True


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError("Claude response did not contain JSON") from None
        return json.loads(match.group(0))


def _is_commercial_tender(tender: ScoredTender) -> bool:
    return tender.__tablename__ == "commercial_tenders"


def _is_merx_tender(tender: ScoredTender) -> bool:
    return (
        tender.__tablename__ == "tenders"
        and (getattr(tender, "source", "") or "").strip().lower() == MERX_SOURCE
    )


def _needs_ai_scoring_filter(model: ScoredModel):
    """Rows missing a usable AI score (NULL/0 score or empty summary)."""
    return or_(
        model.ai_score.is_(None),
        model.ai_score == 0,
        model.ai_summary.is_(None),
        model.ai_summary == "",
    )


def _is_federal(tender: ScoredTender) -> bool:
    return tender.__tablename__ == "tenders" and not _is_merx_tender(tender)


def _source_filter(model: ScoredModel, source: str):
    return func.lower(model.source) == source.lower()


def _tender_company(tender: ScoredTender) -> str:
    company = getattr(tender, "company", None) or getattr(tender, "organization", None)
    return str(company or "").strip()


def _tender_value(tender: ScoredTender) -> str:
    value = getattr(tender, "value", None) or getattr(tender, "estimated_value", None)
    return str(value or "").strip()


def _tender_deadline(tender: ScoredTender) -> str:
    deadline = getattr(tender, "deadline", None) or getattr(tender, "closing_date", None)
    return str(deadline or "").strip()


def _tender_description(tender: ScoredTender) -> str:
    description = getattr(tender, "description", None)
    if description and str(description).strip():
        return str(description).strip()

    parts = [
        part
        for part in (
            tender.category,
            getattr(tender, "status", None),
            getattr(tender, "location", None),
        )
        if part and str(part).strip()
    ]
    return " · ".join(parts) if parts else "Not provided"


def _build_scoring_prompt(tender: ScoredTender) -> str:
    source = getattr(tender, "source", "") or "unknown"
    value = _tender_value(tender)
    needs_budget = _value_is_empty(value)
    budget_instruction = (
        'Include a realistic CAD budget_range string (e.g. "$500K–$2M") because value is missing.'
        if needs_budget
        else "Set budget_range to null because a value is already provided."
    )

    if _is_federal(tender):
        audience = (
            "Score this Canadian federal procurement opportunity for British Columbia "
            "construction and services contractors."
        )
    elif _is_commercial_tender(tender) or _is_merx_tender(tender):
        audience = (
            "Score this British Columbia commercial/provincial procurement opportunity "
            "for construction contractors and builders."
        )
    else:
        audience = (
            "Score this British Columbia procurement opportunity for architecture and engineering firms "
            "(architectural design, structural, civil, planning, building engineering)."
        )

    status_line = ""
    if not _is_federal(tender):
        status_line = f"Status: {getattr(tender, 'status', None) or 'Unknown'}\n"

    location_line = ""
    if _is_federal(tender) or _is_merx_tender(tender):
        location_line = f"Location: {getattr(tender, 'location', None) or 'Not stated'}\n"

    return f"""{audience}

Title: {tender.title}
Organization: {_tender_company(tender)}
Description: {_tender_description(tender)}
Category: {tender.category}
Value: {value or "Not stated"}
Deadline: {_tender_deadline(tender) or "Not stated"}
{status_line}{location_line}Source: {source}

Return JSON only with this shape:
{{
  "score": <integer 1-10>,
  "analysis": "<exactly two sentences explaining fit for BC contractors>",
  "budget_range": <string or null>
}}

{budget_instruction}
Score 1 = poor fit; 10 = excellent fit."""


def _build_budget_prompt(tender: ScoredTender) -> str:
    source = getattr(tender, "source", "") or "unknown"
    return f"""Estimate a realistic CAD budget range for this British Columbia procurement opportunity based on the title, organization, and description.

Title: {tender.title}
Organization: {_tender_company(tender)}
Description: {_tender_description(tender)}
Source: {source}

Return JSON only with this shape:
{{
  "budget_range": "<string like $500K–$2M>"
}}"""


def _normalize_budget_range(value: Any) -> str:
    if value is None:
        return ""
    budget = str(value).strip()
    return budget


def _score_tender(
    client: anthropic.Anthropic,
    tender: ScoredTender,
) -> tuple[int, str, str]:
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=300,
        messages=[{"role": "user", "content": _build_scoring_prompt(tender)}],
    )
    text_blocks = [block.text for block in response.content if block.type == "text"]
    if not text_blocks:
        raise ValueError("Claude returned no text content")

    payload = _extract_json(text_blocks[0])
    score = int(payload.get("score", 0))
    score = max(1, min(10, score))
    analysis = str(payload.get("analysis", "")).strip()
    if not analysis:
        raise ValueError("Claude response missing analysis")

    budget_estimate = ""
    if _value_is_empty(_tender_value(tender)):
        budget_estimate = _normalize_budget_range(payload.get("budget_range"))

    return score, analysis, budget_estimate


def _estimate_budget(client: anthropic.Anthropic, tender: ScoredTender) -> str:
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=150,
        messages=[{"role": "user", "content": _build_budget_prompt(tender)}],
    )
    text_blocks = [block.text for block in response.content if block.type == "text"]
    if not text_blocks:
        raise ValueError("Claude returned no text content")

    payload = _extract_json(text_blocks[0])
    budget_estimate = _normalize_budget_range(payload.get("budget_range"))
    if not budget_estimate:
        raise ValueError("Claude response missing budget_range")
    return budget_estimate


def _count_unscored(session: Session, model: ScoredModel, *, extra_filter=None) -> int:
    query = select(func.count()).select_from(model).where(_needs_ai_scoring_filter(model))
    if extra_filter is not None:
        query = query.where(extra_filter)
    return int(session.scalar(query) or 0)


def _score_table(
    session: Session,
    client: anthropic.Anthropic,
    model: ScoredModel,
    *,
    extra_filter=None,
    label: str | None = None,
    budget: _ScoringRunBudget | None = None,
    request_reserve_seconds: float = DEFAULT_ANTHROPIC_TIMEOUT_SECONDS,
) -> int:
    batch_limit = _ai_batch_limit()
    backlog = _count_unscored(session, model, extra_filter=extra_filter)
    query = select(model).where(_needs_ai_scoring_filter(model))
    if extra_filter is not None:
        query = query.where(extra_filter)
    rows = session.scalars(query.limit(batch_limit)).all()
    table_label = label or model.__tablename__
    logger.info(
        "[AI Scoring] %s backlog=%s processing=%s batch_limit=%s",
        table_label,
        backlog,
        len(rows),
        batch_limit,
    )
    scored = 0

    for index, tender in enumerate(rows, start=1):
        if budget is not None and not budget.can_start_request(request_reserve_seconds):
            logger.warning(
                "[AI Scoring] Time budget exhausted before %s %s/%s; returning partial counts",
                table_label,
                index,
                len(rows),
            )
            break
        logger.info("[AI Scoring] %s %s/%s: %s", table_label, index, len(rows), tender.title[:70])
        try:
            score, summary, budget_estimate = _score_tender(client, tender)
            tender.ai_score = score
            tender.ai_summary = summary
            if budget_estimate:
                tender.ai_budget_estimate = budget_estimate
            session.commit()
            scored += 1
        except Exception as exc:
            session.rollback()
            logger.warning("[AI Scoring] Failed (%s): %s", table_label, exc)

        if budget is None or budget.can_sleep(SCORING_DELAY_SECONDS):
            time.sleep(SCORING_DELAY_SECONDS)

    logger.info("[AI Scoring] %s scored=%s attempted=%s", table_label, scored, len(rows))
    return scored


def _estimate_budgets_table(
    session: Session,
    client: anthropic.Anthropic,
    model: ScoredModel,
    *,
    extra_filter=None,
    budget: _ScoringRunBudget | None = None,
    request_reserve_seconds: float = DEFAULT_ANTHROPIC_TIMEOUT_SECONDS,
) -> int:
    batch_limit = _ai_batch_limit()
    estimated = 0
    processed_ids: set[int] = set()

    while estimated < batch_limit:
        if budget is not None and not budget.can_start_request(request_reserve_seconds):
            logger.warning(
                "[AI Budget] Time budget exhausted before querying %s; returning partial counts",
                model.__tablename__,
            )
            break
        remaining = batch_limit - estimated
        query = select(model).where(
            or_(model.ai_budget_estimate.is_(None), model.ai_budget_estimate == "")
        )
        if extra_filter is not None:
            query = query.where(extra_filter)
        candidates = session.scalars(query.limit(remaining * 4)).all()
        targets: list = []
        for tender in candidates:
            if tender.id in processed_ids:
                continue
            processed_ids.add(tender.id)
            if not _value_is_empty(_tender_value(tender)):
                continue
            if (tender.ai_budget_estimate or "").strip():
                continue
            targets.append(tender)
            if len(targets) >= remaining:
                break

        if not targets:
            break

        for tender in targets:
            if budget is not None and not budget.can_start_request(request_reserve_seconds):
                logger.warning(
                    "[AI Budget] Time budget exhausted before %s; returning partial counts",
                    tender.title[:70],
                )
                return estimated
            label = model.__tablename__
            logger.info(
                "[AI Budget] %s %s/%s: %s",
                label,
                estimated + 1,
                batch_limit,
                tender.title[:70],
            )
            try:
                tender.ai_budget_estimate = _estimate_budget(client, tender)
                session.commit()
                estimated += 1
            except Exception as exc:
                session.rollback()
                logger.warning("[AI Budget] Failed (%s): %s", label, exc)

            if budget is None or budget.can_sleep(SCORING_DELAY_SECONDS):
                time.sleep(SCORING_DELAY_SECONDS)

    return estimated


def score_unscored_tenders(
    session: Session,
    *,
    time_budget_seconds: float | None = None,
) -> dict[str, Any]:
    batch_limit = _ai_batch_limit()
    api_key = get_anthropic_api_key()
    budget = _ScoringRunBudget(time_budget_seconds)
    request_reserve_seconds = _anthropic_timeout_seconds() + SCORING_DELAY_SECONDS
    if not api_key:
        hint = (
            " Add ANTHROPIC_API_KEY to this Railway service's environment variables."
            if os.getenv("RAILWAY_ENVIRONMENT_NAME")
            else " Set ANTHROPIC_API_KEY in your environment or .env file."
        )
        logger.warning("[AI Scoring] Skipping: ANTHROPIC_API_KEY is not set.%s", hint)
        return {
            "tenders_scored": 0,
            "federal_gov_tenders_scored": 0,
            "merx_tenders_scored": 0,
            "arch_tenders_scored": 0,
            "bidcentral_tenders_scored": 0,
            "commercial_tenders_scored": 0,
            "tenders_budgeted": 0,
            "federal_gov_tenders_budgeted": 0,
            "merx_tenders_budgeted": 0,
            "arch_tenders_budgeted": 0,
            "commercial_tenders_budgeted": 0,
            "bidcentral_tenders_budgeted": 0,
            "total_tenders_scored": 0,
            "total_tenders_budgeted": 0,
            "batch_limit_per_table": batch_limit,
            "time_budget_seconds": time_budget_seconds,
            "time_budget_exhausted": False,
            "backlog": {},
        }

    client = _anthropic_client(api_key)
    federal_gov_filter = _source_filter(Tender, FEDERAL_GOV_SOURCE)
    merx_filter = _source_filter(Tender, MERX_SOURCE)

    backlog = {
        "federal_gov": _count_unscored(session, Tender, extra_filter=federal_gov_filter),
        "merx_provincial": _count_unscored(session, Tender, extra_filter=merx_filter),
        "commercial_tenders": _count_unscored(session, CommercialTender),
        "arch_tenders": _count_unscored(session, ArchTender),
    }
    logger.info(
        "[AI Scoring] Starting run batch_limit_per_table=%s backlog=%s",
        batch_limit,
        backlog,
    )

    federal_gov_scored = _score_table(
        session,
        client,
        Tender,
        extra_filter=federal_gov_filter,
        label="federal_gov",
        budget=budget,
        request_reserve_seconds=request_reserve_seconds,
    )
    merx_scored = _score_table(
        session,
        client,
        Tender,
        extra_filter=merx_filter,
        label="merx_provincial",
        budget=budget,
        request_reserve_seconds=request_reserve_seconds,
    )
    arch_scored = _score_table(
        session,
        client,
        ArchTender,
        budget=budget,
        request_reserve_seconds=request_reserve_seconds,
    )
    bidcentral_scored = _score_table(
        session,
        client,
        CommercialTender,
        label="commercial_tenders",
        budget=budget,
        request_reserve_seconds=request_reserve_seconds,
    )

    logger.info("[AI Budget] Estimating budgets for tenders with missing values...")
    federal_gov_budgeted = _estimate_budgets_table(
        session,
        client,
        Tender,
        extra_filter=federal_gov_filter,
        budget=budget,
        request_reserve_seconds=request_reserve_seconds,
    )
    merx_budgeted = _estimate_budgets_table(
        session,
        client,
        Tender,
        extra_filter=merx_filter,
        budget=budget,
        request_reserve_seconds=request_reserve_seconds,
    )
    arch_budgeted = _estimate_budgets_table(
        session,
        client,
        ArchTender,
        budget=budget,
        request_reserve_seconds=request_reserve_seconds,
    )
    bidcentral_budgeted = _estimate_budgets_table(
        session,
        client,
        CommercialTender,
        budget=budget,
        request_reserve_seconds=request_reserve_seconds,
    )

    tenders_scored = federal_gov_scored + merx_scored
    commercial_tenders_scored = bidcentral_scored + merx_scored
    total_scored = tenders_scored + arch_scored + bidcentral_scored
    total_budgeted = federal_gov_budgeted + merx_budgeted + arch_budgeted + bidcentral_budgeted
    logger.info(
        "[AI Scoring] Run complete total_scored=%s "
        "(federal_gov=%s merx=%s bidcentral=%s arch=%s) budgeted=%s",
        total_scored,
        federal_gov_scored,
        merx_scored,
        bidcentral_scored,
        arch_scored,
        total_budgeted,
    )

    return {
        "tenders_scored": tenders_scored,
        "federal_gov_tenders_scored": federal_gov_scored,
        "merx_tenders_scored": merx_scored,
        "arch_tenders_scored": arch_scored,
        "bidcentral_tenders_scored": bidcentral_scored,
        "commercial_tenders_scored": commercial_tenders_scored,
        "tenders_budgeted": federal_gov_budgeted + merx_budgeted,
        "federal_gov_tenders_budgeted": federal_gov_budgeted,
        "merx_tenders_budgeted": merx_budgeted,
        "arch_tenders_budgeted": arch_budgeted,
        "commercial_tenders_budgeted": bidcentral_budgeted + merx_budgeted,
        "bidcentral_tenders_budgeted": bidcentral_budgeted,
        "total_tenders_scored": total_scored,
        "total_tenders_budgeted": total_budgeted,
        "batch_limit_per_table": batch_limit,
        "time_budget_seconds": time_budget_seconds,
        "time_budget_exhausted": budget.exhausted,
        "backlog": backlog,
    }
