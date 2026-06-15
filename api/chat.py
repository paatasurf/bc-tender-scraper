from __future__ import annotations

import anthropic
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from config.env import get_anthropic_api_key, get_env
from db.connection import get_session
from db.models import Tender

router = APIRouter()

CHAT_MODEL = get_env("ANTHROPIC_CHAT_MODEL", "claude-haiku-4-5-20251001")
CHAT_MAX_TOKENS = int(get_env("ANTHROPIC_CHAT_MAX_TOKENS", "1000"))
CHAT_TENDER_LIMIT = int(get_env("ANTHROPIC_CHAT_TENDER_LIMIT", "30"))


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)


class ChatResponse(BaseModel):
    response: str


def fetch_recent_tenders(limit: int = 30) -> list[dict[str, str]]:
    session = get_session()
    try:
        rows = session.scalars(select(Tender).order_by(Tender.id.desc()).limit(limit)).all()
        return [
            {
                "title": row.title or "",
                "address": row.location or "",
                "category": row.category or "",
                "deadline": row.closing_date or "",
                "value": row.estimated_value or row.ai_budget_estimate or "",
                "source": row.source or "",
            }
            for row in rows
        ]
    finally:
        session.close()


def _build_system_prompt(tenders: list[dict[str, str]]) -> str:
    ctx = "\n".join(
        f"- {t['title']} | {t['address']} | {t['category']} | "
        f"deadline: {t['deadline']} | value: {t['value']} | source: {t['source']}"
        for t in tenders
    )
    if not ctx:
        ctx = "(No tenders currently in database.)"

    return f"""You are the TenderScope assistant, embedded in a LIVE BC construction tender platform.
You DO have access to real tender data — it is provided below from TenderScope's own database.
NEVER tell the user to visit BC Bid, MERX, Biddingo or any external site.
NEVER say you lack access to data. Answer ONLY from the tenders below.
If something isn't in the data, say so plainly.

CURRENT TENDERS:
{ctx}
"""


@router.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest) -> ChatResponse:
    api_key = get_anthropic_api_key()
    if not api_key:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY is not configured")

    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is empty")

    tenders = fetch_recent_tenders(limit=CHAT_TENDER_LIMIT)
    system = _build_system_prompt(tenders)

    client = anthropic.Anthropic(api_key=api_key)
    try:
        result = client.messages.create(
            model=CHAT_MODEL,
            max_tokens=CHAT_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": message}],
        )
    except anthropic.RateLimitError as exc:
        raise HTTPException(status_code=429, detail="Claude rate limit exceeded") from exc
    except anthropic.APIStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Claude API error: {exc}") from exc
    except anthropic.APIConnectionError as exc:
        raise HTTPException(status_code=502, detail="Could not connect to Claude API") from exc

    text = "".join(block.text for block in result.content if block.type == "text").strip()
    if not text:
        raise HTTPException(status_code=502, detail="Claude returned an empty response")

    return ChatResponse(response=text)
