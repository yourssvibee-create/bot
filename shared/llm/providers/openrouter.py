"""OpenRouter provider — OpenAI-compatible at openrouter.ai/api/v1."""
from __future__ import annotations

from typing import Type, TypeVar

from pydantic import BaseModel

from ...config import get, is_placeholder
from ..base import Provider, ProviderError
from ._openai_compat import chat_completion, extract_content, validate_json

T = TypeVar("T", bound=BaseModel)


class OpenRouterProvider(Provider):
    name = "openrouter"
    base_url = "https://openrouter.ai/api/v1"

    def enabled(self) -> bool:
        return not is_placeholder(get("OPENROUTER_API_KEY"))

    def model_for(self, role: str) -> str:
        if role == "draft":
            return get("OPENROUTER_MODEL_DRAFT",
                      "anthropic/claude-haiku-5.5")  # type: ignore[return-value]
        return get("OPENROUTER_MODEL_SCORE",
                  "anthropic/claude-haiku-5.5")  # type: ignore[return-value]

    def _headers(self) -> dict[str, str]:
        # Optional attribution headers; not required but recommended.
        return {
            "HTTP-Referer": get("OPENROUTER_REFERER", "https://ominvo.com") or "https://ominvo.com",
            "X-Title": get("OPENROUTER_TITLE", "Ominvo Bot 1") or "Ominvo Bot 1",
        }

    async def draft(self, system: str, user: str, *,
                    temperature: float = 0.7, max_tokens: int = 1600) -> str:
        data = await chat_completion(
            base_url=self.base_url,
            api_key=get("OPENROUTER_API_KEY", required=True),  # type: ignore[arg-type]
            model=self.model_for("draft"),
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            temperature=temperature,
            max_tokens=max_tokens,
            extra_headers=self._headers(),
        )
        text = extract_content(data)
        if not text:
            raise ProviderError("empty draft content", retryable=True)
        return text

    async def score_json(self, system: str, user: str, schema: Type[T], *,
                         temperature: float = 0.0, max_tokens: int = 1200) -> T:
        data = await chat_completion(
            base_url=self.base_url,
            api_key=get("OPENROUTER_API_KEY", required=True),  # type: ignore[arg-type]
            model=self.model_for("score"),
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=True,
            extra_headers=self._headers(),
        )
        return validate_json(extract_content(data), schema)
