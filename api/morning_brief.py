"""
POST /internal/morning-brief — generate and email a TenderScope morning brief.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from intelligence.morning_brief import send_morning_brief

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"])


class MorningBriefRequest(BaseModel):
    company_id: int = Field(..., ge=1, description="companies.id for the brief target")
    email: str = Field(..., min_length=3, description="Recipient email address")


@router.post("/morning-brief")
def morning_brief(body: MorningBriefRequest) -> dict[str, Any]:
    """
    1. Call voice-n8n-agent /api/chat for the morning brief narrative.
    2. Format as a dark-themed HTML email.
    3. Send via Resend.
    """
    try:
        return send_morning_brief(company_id=body.company_id, email=str(body.email))
    except RuntimeError as exc:
        logger.warning(
            "[MorningBrief] Failed company_id=%s email=%s: %s",
            body.company_id,
            body.email,
            exc,
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
