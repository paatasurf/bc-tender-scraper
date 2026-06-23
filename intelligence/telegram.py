"""
intelligence/telegram.py
────────────────────────
Telegram Bot notification helper.

Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from env.
Silently skips (logs a debug line) when either variable is missing.
"""
from __future__ import annotations

import logging

import requests

from config.env import get_env

logger = logging.getLogger(__name__)


def send_telegram_message(text: str) -> bool:
    """
    Send *text* to the configured Telegram chat.

    Returns True on success, False if env vars are absent or the request fails.
    Failures are logged as warnings and never raise.
    """
    token = get_env("TELEGRAM_BOT_TOKEN")
    chat_id = get_env("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.debug("[Telegram] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not configured — skipping")
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
        resp.raise_for_status()
        logger.info("[Telegram] Notification sent (chat_id=%s)", chat_id)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Telegram] Failed to send notification: %s", exc)
        return False
