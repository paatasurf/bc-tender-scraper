"""
intelligence/resend.py
──────────────────────
Resend email API helper.

Reads RESEND_API_KEY from env.  Default from address: alerts@tenderscope.ca
"""
from __future__ import annotations

import logging

import requests

from config.env import get_env

logger = logging.getLogger(__name__)

DEFAULT_FROM_EMAIL = "alerts@tenderscope.ca"
RESEND_API_URL = "https://api.resend.com/emails"


def send_email(
    to: str,
    subject: str,
    html: str,
    *,
    from_email: str | None = None,
) -> dict:
    """
    Send an email via Resend.

    Returns the Resend API response dict on success.
    Raises RuntimeError when RESEND_API_KEY is missing or the request fails.
    """
    api_key = get_env("RESEND_API_KEY")
    if not api_key:
        raise RuntimeError("RESEND_API_KEY is not configured")

    sender = from_email or get_env("RESEND_FROM_EMAIL", DEFAULT_FROM_EMAIL)
    payload = {
        "from": sender,
        "to": [to],
        "subject": subject,
        "html": html,
    }

    try:
        resp = requests.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        logger.info("[Resend] Email sent to %s id=%s", to, data.get("id"))
        return data
    except requests.RequestException as exc:
        detail = ""
        if hasattr(exc, "response") and exc.response is not None:
            detail = exc.response.text[:300]
        logger.warning("[Resend] Failed to send to %s: %s %s", to, exc, detail)
        raise RuntimeError(f"Resend API error: {exc}") from exc
