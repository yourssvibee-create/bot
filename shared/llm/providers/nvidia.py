"""NVIDIA NIM provider — OpenAI-compatible at integrate.api.nvidia.com/v1."""
from __future__ import annotations

from typing import Type, TypeVar

from pydantic import BaseModel

from ...config import get, is_placeholder
from ..base import Provider, ProviderError
from ._openai_compat import chat_completion, extract_content, validate_json

T = TypeVar("T", bound=BaseModel)


class NvidiaProvider(Provider):
    name = "nvidia"
    base_url = "https://integrate.api.nvidia.com/v1"

    def enabled(self) -> bool:
        return not is_placeholder(get("NVIDIA_API_KEY"))

    def model_for(self, role: str) -> str:
        if role == "draft":
            return get("NVIDIA_MODEL_DRAFT", "nvidia/nemotron-3.5-super-vl-120b-a12b")  # type: ignore[return-value]
        return get("NVIDIA_MODEL_SCORE", "nvidia/nemotron-3.5-super-vl-120b-a12b")  # type: ignore[return-value]

    async def draft(self, system: str, user: str, *,
                    temperature: float = 0.7, max_tokens: int = 1600) -> str:
        data = await chat_completion(
            base_url=self.base_url,
            api_key=get("NVIDIA_API_KEY", required=True),  # type: ignore[arg-type]
            model=self.model_for("draft"),
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        text = extract_content(data)
        if not text:
            raise ProviderError("empty draft content", retryable=True)
        return text

    async def score_json(self, system: str, user: str, schema: Type[T], *,
                         temperature: float = 0.0, max_tokens: int = 1200) -> T:
        # NVIDIA NIM accepts OpenAI's response_format=json_object on the hosted
        # llama-3.x-instruct families. If a specific model rejects it, the
        # error is retryable and the router falls through to the next provider.
        data = await chat_completion(
            base_url=self.base_url,
            api_key=get("NVIDIA_API_KEY", required=True),  # type: ignore[arg-type]
            model=self.model_for("score"),
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=True,
        )
        return validate_json(extract_content(data), schema)
