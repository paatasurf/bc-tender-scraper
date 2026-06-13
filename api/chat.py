from __future__ import annotations

import anthropic
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from config.env import get_anthropic_api_key, get_env

router = APIRouter()

CHAT_MODEL = get_env("ANTHROPIC_CHAT_MODEL", "claude-sonnet-4-5")
CHAT_MAX_TOKENS = int(get_env("ANTHROPIC_CHAT_MAX_TOKENS", "1024"))
CHAT_SYSTEM_PROMPT = get_env(
    "ANTHROPIC_CHAT_SYSTEM_PROMPT",
    "You are TenderScope, an assistant for BC construction and architecture market intelligence. "
    "Answer clearly and concisely using general knowledge; say when live tender data is needed.",
)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)


class ChatResponse(BaseModel):
    response: str


@router.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest) -> ChatResponse:
    api_key = get_anthropic_api_key()
    if not api_key:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY is not configured")

    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is empty")

    client = anthropic.Anthropic(api_key=api_key)
    try:
        result = client.messages.create(
            model=CHAT_MODEL,
            max_tokens=CHAT_MAX_TOKENS,
            system=CHAT_SYSTEM_PROMPT,
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
