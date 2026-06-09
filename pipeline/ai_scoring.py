from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import anthropic
from sqlalchemy import select
from sqlalchemy.orm import Session

from config.env import get_anthropic_api_key
from db.models import ArchTender, CommercialTender

CLAUDE_MODEL = "claude-sonnet-4-20250514"
SCORING_DELAY_SECONDS = 0.5


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


def _tender_description(tender: ArchTender | CommercialTender) -> str:
    description = getattr(tender, "description", None)
    if description and str(description).strip():
        return str(description).strip()

    parts = [part for part in (tender.category, tender.status) if part and str(part).strip()]
    return " · ".join(parts) if parts else "Not provided"


def _build_scoring_prompt(tender: ArchTender | CommercialTender) -> str:
    source = getattr(tender, "source", "") or "unknown"
    needs_budget = _value_is_empty(tender.value)
    budget_instruction = (
        'Include a realistic CAD budget_range string (e.g. "$500K–$2M") because value is missing.'
        if needs_budget
        else "Set budget_range to null because a value is already provided."
    )

    return f"""Score this British Columbia procurement opportunity for architecture and engineering firms (architectural design, structural, civil, planning, building engineering).

Title: {tender.title}
Organization: {tender.company}
Description: {_tender_description(tender)}
Category: {tender.category}
Value: {tender.value or "Not stated"}
Deadline: {tender.deadline or "Not stated"}
Status: {tender.status or "Unknown"}
Source: {source}

Return JSON only with this shape:
{{
  "score": <integer 1-10>,
  "analysis": "<exactly two sentences explaining fit for BC architecture/engineering firms>",
  "budget_range": <string or null>
}}

{budget_instruction}
Score 1 = poor fit; 10 = excellent fit."""


def _build_budget_prompt(tender: ArchTender | CommercialTender) -> str:
    source = getattr(tender, "source", "") or "unknown"
    return f"""Estimate a realistic CAD budget range for this British Columbia procurement opportunity based on the title, company, and description.

Title: {tender.title}
Company: {tender.company}
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
    tender: ArchTender | CommercialTender,
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
    if _value_is_empty(tender.value):
        budget_estimate = _normalize_budget_range(payload.get("budget_range"))

    return score, analysis, budget_estimate


def _estimate_budget(client: anthropic.Anthropic, tender: ArchTender | CommercialTender) -> str:
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
    rows = session.scalars(select(model).where(model.ai_score.is_(None))).all()
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
    rows = session.scalars(select(model)).all()
    targets = [
        tender
        for tender in rows
        if _value_is_empty(tender.value) and not (tender.ai_budget_estimate or "").strip()
    ]
    estimated = 0

    for index, tender in enumerate(targets, start=1):
        label = model.__tablename__
        print(f"[AI Budget] {label} {index}/{len(targets)}: {tender.title[:70]}")
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
            "arch_tenders_scored": 0,
            "commercial_tenders_scored": 0,
            "arch_tenders_budgeted": 0,
            "commercial_tenders_budgeted": 0,
        }

    client = anthropic.Anthropic(api_key=api_key)
    print("[AI Scoring] Scoring unscored architecture and commercial tenders...")

    arch_scored = _score_table(session, client, ArchTender)
    commercial_scored = _score_table(session, client, CommercialTender)

    print("[AI Budget] Estimating budgets for tenders with missing values...")
    arch_budgeted = _estimate_budgets_table(session, client, ArchTender)
    commercial_budgeted = _estimate_budgets_table(session, client, CommercialTender)

    return {
        "arch_tenders_scored": arch_scored,
        "commercial_tenders_scored": commercial_scored,
        "arch_tenders_budgeted": arch_budgeted,
        "commercial_tenders_budgeted": commercial_budgeted,
    }
