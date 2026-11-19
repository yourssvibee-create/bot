"""Gemini provider — uses the google-genai SDK (JSON mode via response_schema).

Falls back to google-generativeai (the older SDK) if the newer one isn't
installed; both expose equivalent primitives for what we use.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Type, TypeVar

from pydantic import BaseModel, ValidationError

from ...config import get, is_placeholder
from ..base import Provider, ProviderError

T = TypeVar("T", bound=BaseModel)


def _client():
    try:
        # Preferred: new unified SDK
        from google import genai  # type: ignore
        return ("genai", genai.Client(api_key=get("GEMINI_API_KEY", required=True)))
    except ImportError:
        pass
    try:
        import google.generativeai as legacy  # type: ignore
        legacy.configure(api_key=get("GEMINI_API_KEY", required=True))
        return ("legacy", legacy)
    except ImportError as e:
        raise ProviderError(
            "neither 'google-genai' nor 'google-generativeai' is installed",
            retryable=False) from e


class GeminiProvider(Provider):
    name = "gemini"

    def enabled(self) -> bool:
        return not is_placeholder(get("GEMINI_API_KEY"))

    def model_for(self, role: str) -> str:
        if role == "draft":
            return get("GEMINI_MODEL_DRAFT", "gemini-2.0-flash-exp")  # type: ignore[return-value]
        return get("GEMINI_MODEL_SCORE", "gemini-2.0-flash-exp")  # type: ignore[return-value]

    async def draft(self, system: str, user: str, *,
                    temperature: float = 0.7, max_tokens: int = 1600) -> str:
        return await asyncio.to_thread(self._draft_sync, system, user,
                                       temperature, max_tokens)

    def _draft_sync(self, system: str, user: str,
                    temperature: float, max_tokens: int) -> str:
        kind, client = _client()
        model = self.model_for("draft")
        try:
            if kind == "genai":
                from google.genai import types  # type: ignore
                cfg = types.GenerateContentConfig(
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                    system_instruction=system,
                )
                resp = client.models.generate_content(
                    model=model, contents=user, config=cfg,
                )
                text = (getattr(resp, "text", None) or "").strip()
            else:
                m = client.GenerativeModel(model_name=model, system_instruction=system)
                resp = m.generate_content(
                    user,
                    generation_config={"temperature": temperature,
                                       "max_output_tokens": max_tokens},
                )
                text = (getattr(resp, "text", None) or "").strip()
        except Exception as e:
            raise ProviderError(f"gemini error: {e}", retryable=True) from e
        if not text:
            raise ProviderError("empty draft content", retryable=True)
        return text

    async def score_json(self, system: str, user: str, schema: Type[T], *,
                         temperature: float = 0.0, max_tokens: int = 1200) -> T:
        content = await asyncio.to_thread(self._score_sync, system, user, schema,
                                          temperature, max_tokens)
        return content

    def _score_sync(self, system: str, user: str, schema: Type[T],
                    temperature: float, max_tokens: int) -> T:
        kind, client = _client()
        model = self.model_for("score")
        try:
            if kind == "genai":
                from google.genai import types  # type: ignore
                cfg = types.GenerateContentConfig(
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                    system_instruction=system,
                    response_mime_type="application/json",
                    response_schema=schema,
                )
                resp = client.models.generate_content(
                    model=model, contents=user, config=cfg,
                )
                # new SDK returns a parsed object on .parsed; fall back to .text
                parsed = getattr(resp, "parsed", None)
                if isinstance(parsed, schema):
                    return parsed
                raw = (getattr(resp, "text", None) or "").strip()
            else:
                m = client.GenerativeModel(model_name=model, system_instruction=system)
                resp = m.generate_content(
                    user,
                    generation_config={
                        "temperature": temperature,
                        "max_output_tokens": max_tokens,
                        "response_mime_type": "application/json",
                    },
                )
                raw = (getattr(resp, "text", None) or "").strip()
        except Exception as e:
            raise ProviderError(f"gemini error: {e}", retryable=True) from e

        if not raw:
            raise ProviderError("empty JSON content", retryable=True)
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ProviderError(f"invalid JSON: {e}; raw={raw[:200]}", retryable=True)
        try:
            return schema.model_validate(obj)
        except ValidationError as e:
            raise ProviderError(f"schema validation failed: {e}; raw={raw[:200]}",
                                retryable=True)
