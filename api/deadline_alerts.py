"""
POST /internal/deadline-alerts — email alerts for tenders closing in 1, 3, or 7 days.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from intelligence.deadline_alerts import send_all_deadline_alerts

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"])


@router.post("/deadline-alerts")
def deadline_alerts() -> dict[str, Any]:
    """
    For each client profile with alerts enabled:
    - load tender_matches for the company
    - email Resend alerts when a matched tender closes in 1, 3, or 7 days
    """
    try:
        return send_all_deadline_alerts()
    except RuntimeError as exc:
        logger.warning("[DeadlineAlerts] Batch failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
