"""Async Groq wrapper: Llama 3.3 70B for drafting, 3.1 8B Instant for scoring.

Retries on 429 with exponential backoff. Scoring uses JSON mode validated
through a caller-supplied Pydantic schema.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Type, TypeVar

import httpx
from pydantic import BaseModel, ValidationError
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .config import get
from .logging import get_logger

log = get_logger("groq")

MODEL_DRAFT = "llama-3.3-70b-versatile"
MODEL_SCORE = "llama-3.1-8b-instant"

_BASE = "https://api.groq.com/openai/v1/chat/completions"
T = TypeVar("T", bound=BaseModel)


class GroqRateLimit(Exception):
    pass


class GroqError(Exception):
    pass


def _headers() -> dict[str, str]:
    key = get("GROQ_API_KEY", required=True)
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


async def _call(payload: dict[str, Any], timeout: float = 60.0) -> dict[str, Any]:
    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1.5, min=2, max=30),
        retry=retry_if_exception_type(GroqRateLimit),
        reraise=True,
    ):
        with attempt:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(_BASE, headers=_headers(), json=payload)
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("retry-after", "2"))
                log.warning("groq_rate_limited", extra={"retry_after": retry_after})
                await asyncio.sleep(retry_after)
                raise GroqRateLimit("429")
            if resp.status_code >= 400:
                raise GroqError(f"groq {resp.status_code}: {resp.text[:400]}")
            return resp.json()
    raise GroqError("exhausted retries")


async def draft(
    system: str,
    user: str,
    *,
    temperature: float = 0.7,
    max_tokens: int = 500,
) -> str:
    """Return free-form text from the 70B model."""
    payload = {
        "model": MODEL_DRAFT,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    data = await _call(payload)
    return data["choices"][0]["message"]["content"].strip()


async def score_json(
    system: str,
    user: str,
    schema: Type[T],
    *,
    temperature: float = 0.0,
    max_tokens: int = 400,
) -> T:
    """Call the 8B model in JSON mode and validate against `schema`."""
    payload = {
        "model": MODEL_SCORE,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    data = await _call(payload)
    content = data["choices"][0]["message"]["content"]
    try:
        obj = json.loads(content)
        return schema.model_validate(obj)
    except (json.JSONDecodeError, ValidationError) as e:
        raise GroqError(f"invalid json/schema: {e}; raw={content[:400]}")
