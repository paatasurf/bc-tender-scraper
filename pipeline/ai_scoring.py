from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import anthropic
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from config.env import get_anthropic_api_key, get_env
from db.models import ArchTender, CommercialTender, Tender

CLAUDE_MODEL = "claude-sonnet-4-5"
SCORING_DELAY_SECONDS = 0.5
DEFAULT_AI_BATCH_LIMIT = 50

ScoredTender = ArchTender | CommercialTender | Tender


def _ai_batch_limit() -> int:
    raw = get_env("AI_SCORING_MAX_PER_RUN", str(DEFAULT_AI_BATCH_LIMIT))
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_AI_BATCH_LIMIT


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


def _is_federal(tender: ScoredTender) -> bool:
    return tender.__tablename__ == "tenders"


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
    else:
        audience = (
            "Score this British Columbia procurement opportunity for architecture and engineering firms "
            "(architectural design, structural, civil, planning, building engineering)."
        )

    status_line = ""
    if not _is_federal(tender):
        status_line = f"Status: {getattr(tender, 'status', None) or 'Unknown'}\n"

    location_line = ""
    if _is_federal(tender):
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


def _score_table(session: Session, client: anthropic.Anthropic, model) -> int:
    batch_limit = _ai_batch_limit()
    rows = session.scalars(
        select(model).where(model.ai_score.is_(None)).limit(batch_limit)
    ).all()
    scored = 0

    for index, tender in enumerate(rows, start=1):
        label = model.__tablename__
        print(f"[AI Scoring] {label} {index}/{len(rows)}: {tender.title[:70]}")
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
            print(f"[AI Scoring] Failed ({label}): {exc}")

        time.sleep(SCORING_DELAY_SECONDS)

    return scored


def _estimate_budgets_table(session: Session, client: anthropic.Anthropic, model) -> int:
    batch_limit = _ai_batch_limit()
    estimated = 0
    processed_ids: set[int] = set()

    while estimated < batch_limit:
        remaining = batch_limit - estimated
        candidates = session.scalars(
            select(model)
            .where(or_(model.ai_budget_estimate.is_(None), model.ai_budget_estimate == ""))
            .limit(remaining * 4)
        ).all()
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
            label = model.__tablename__
            print(f"[AI Budget] {label} {estimated + 1}/{batch_limit}: {tender.title[:70]}")
            try:
                tender.ai_budget_estimate = _estimate_budget(client, tender)
                session.commit()
                estimated += 1
            except Exception as exc:
                session.rollback()
                print(f"[AI Budget] Failed ({label}): {exc}")

            time.sleep(SCORING_DELAY_SECONDS)

    return estimated


def score_unscored_tenders(session: Session) -> dict[str, int]:
    api_key = get_anthropic_api_key()
    if not api_key:
        hint = (
            " Add ANTHROPIC_API_KEY to this Railway service's environment variables."
            if os.getenv("RAILWAY_ENVIRONMENT_NAME")
            else " Set ANTHROPIC_API_KEY in your environment or .env file."
        )
        print(f"[AI Scoring] Skipping: ANTHROPIC_API_KEY is not set.{hint}")
        return {
            "tenders_scored": 0,
            "arch_tenders_scored": 0,
            "commercial_tenders_scored": 0,
            "tenders_budgeted": 0,
            "arch_tenders_budgeted": 0,
            "commercial_tenders_budgeted": 0,
        }

    client = anthropic.Anthropic(api_key=api_key)
    print("[AI Scoring] Scoring unscored federal, architecture, and commercial tenders...")

    federal_scored = _score_table(session, client, Tender)
    arch_scored = _score_table(session, client, ArchTender)
    commercial_scored = _score_table(session, client, CommercialTender)

    print("[AI Budget] Estimating budgets for tenders with missing values...")
    federal_budgeted = _estimate_budgets_table(session, client, Tender)
    arch_budgeted = _estimate_budgets_table(session, client, ArchTender)
    commercial_budgeted = _estimate_budgets_table(session, client, CommercialTender)

    return {
        "tenders_scored": federal_scored,
        "arch_tenders_scored": arch_scored,
        "commercial_tenders_scored": commercial_scored,
        "tenders_budgeted": federal_budgeted,
        "arch_tenders_budgeted": arch_budgeted,
        "commercial_tenders_budgeted": commercial_budgeted,
    }
