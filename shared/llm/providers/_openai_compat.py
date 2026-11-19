"""Shared helper for OpenAI-compatible endpoints (Groq, NVIDIA, OpenRouter)."""
from __future__ import annotations

import json
from typing import Any, Type, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from ..base import ProviderError

T = TypeVar("T", bound=BaseModel)


async def chat_completion(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    json_mode: bool = False,
    extra_body: dict[str, Any] | None = None,
    extra_headers: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Minimal OpenAI /chat/completions caller. Returns the parsed JSON body."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if extra_body:
        payload.update(extra_body)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json=payload)

    if resp.status_code == 429:
        raise ProviderError(f"rate limited at {base_url}", retryable=True,
                            status_code=429)
    if resp.status_code >= 500:
        raise ProviderError(f"{resp.status_code} at {base_url}: {resp.text[:200]}",
                            retryable=True, status_code=resp.status_code)
    if resp.status_code >= 400:
        raise ProviderError(f"{resp.status_code} at {base_url}: {resp.text[:400]}",
                            retryable=True, status_code=resp.status_code)
    return resp.json()


def extract_content(data: dict[str, Any]) -> str:
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise ProviderError(f"malformed completion: {str(data)[:300]}", retryable=True)
    return (msg.get("content") or "").strip()


def validate_json(content: str, schema: Type[T]) -> T:
    if not content:
        raise ProviderError("empty JSON content", retryable=True)
    try:
        obj = json.loads(content)
    except json.JSONDecodeError as e:
        raise ProviderError(f"invalid JSON: {e}; raw={content[:200]}", retryable=True)
    try:
        return schema.model_validate(obj)
    except ValidationError as e:
        raise ProviderError(f"schema validation failed: {e}; raw={content[:200]}",
                            retryable=True)


def usage_from(data: dict[str, Any]) -> dict[str, Any]:
    return data.get("usage") or {}
