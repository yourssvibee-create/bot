"""Groq provider — OpenAI-compatible endpoint with reasoning_effort for gpt-oss."""
from __future__ import annotations

from typing import Type, TypeVar

from pydantic import BaseModel

from ...config import get, is_placeholder
from ..base import Provider, ProviderError
from ._openai_compat import chat_completion, extract_content, validate_json

T = TypeVar("T", bound=BaseModel)


class GroqProvider(Provider):
    name = "groq"
    base_url = "https://api.groq.com/openai/v1"

    def enabled(self) -> bool:
        return not is_placeholder(get("GROQ_API_KEY"))

    def model_for(self, role: str) -> str:
        if role == "draft":
            return get("GROQ_MODEL_DRAFT", "openai/gpt-oss-120b")  # type: ignore[return-value]
        return get("GROQ_MODEL_SCORE", "openai/gpt-oss-20b")  # type: ignore[return-value]

    def _reasoning_kwargs(self, model: str) -> dict:
        # gpt-oss-* on Groq is a reasoning model — without reasoning_effort=low
        # every token is burned on the chain-of-thought and `content` is empty.
        if model.startswith("openai/gpt-oss"):
            return {"reasoning_effort": "low"}
        return {}

    async def draft(self, system: str, user: str, *,
                    temperature: float = 0.7, max_tokens: int = 1600) -> str:
        model = self.model_for("draft")
        data = await chat_completion(
            base_url=self.base_url,
            api_key=get("GROQ_API_KEY", required=True),  # type: ignore[arg-type]
            model=model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            temperature=temperature,
            max_tokens=max_tokens,
            extra_body=self._reasoning_kwargs(model),
        )
        text = extract_content(data)
        if not text:
            raise ProviderError("empty draft content", retryable=True)
        return text

    async def score_json(self, system: str, user: str, schema: Type[T], *,
                         temperature: float = 0.0, max_tokens: int = 1200) -> T:
        model = self.model_for("score")
        data = await chat_completion(
            base_url=self.base_url,
            api_key=get("GROQ_API_KEY", required=True),  # type: ignore[arg-type]
            model=model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=True,
            extra_body=self._reasoning_kwargs(model),
        )
        return validate_json(extract_content(data), schema)
