from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import anthropic
from sqlalchemy import select
from sqlalchemy.orm import Session

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


def _build_prompt(tender: ArchTender | CommercialTender) -> str:
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


def _compose_summary(analysis: str, budget_range: str | None, include_budget: bool) -> str:
    analysis = analysis.strip()
    if include_budget and budget_range:
        budget = budget_range.strip()
        if budget:
            return f"{analysis} Estimated budget: {budget}."
    return analysis


def _score_tender(client: anthropic.Anthropic, tender: ArchTender | CommercialTender) -> tuple[int, str]:
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=300,
        messages=[{"role": "user", "content": _build_prompt(tender)}],
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

    budget_range = payload.get("budget_range")
    summary = _compose_summary(
        analysis,
        str(budget_range).strip() if budget_range else None,
        include_budget=_value_is_empty(tender.value),
    )
    return score, summary


def _score_table(session: Session, client: anthropic.Anthropic, model) -> int:
    rows = session.scalars(select(model).where(model.ai_score.is_(None))).all()
    scored = 0

    for index, tender in enumerate(rows, start=1):
        label = model.__tablename__
        print(f"[AI Scoring] {label} {index}/{len(rows)}: {tender.title[:70]}")
        try:
            score, summary = _score_tender(client, tender)
            tender.ai_score = score
            tender.ai_summary = summary
            session.commit()
            scored += 1
        except Exception as exc:
            session.rollback()
            print(f"[AI Scoring] Failed ({label}): {exc}")

        time.sleep(SCORING_DELAY_SECONDS)

    return scored


def score_unscored_tenders(session: Session) -> dict[str, int]:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("[AI Scoring] Skipping: ANTHROPIC_API_KEY is not set")
        return {"arch_tenders": 0, "commercial_tenders": 0}

    client = anthropic.Anthropic(api_key=api_key)
    print("[AI Scoring] Scoring unscored architecture and commercial tenders...")

    return {
        "arch_tenders": _score_table(session, client, ArchTender),
        "commercial_tenders": _score_table(session, client, CommercialTender),
    }
