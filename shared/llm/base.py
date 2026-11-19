"""Provider contract used by the router."""
from __future__ import annotations

import abc
from typing import Any, Type, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class ProviderError(Exception):
    """Raised by a provider when its one attempt failed.

    `retryable` tells the router whether to move to the next provider (True for
    429/5xx/empty-output/JSON-invalid) or to abort the whole chain (False for
    programmer errors).
    """

    def __init__(self, message: str, *, retryable: bool = True,
                 status_code: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


class Provider(abc.ABC):
    """One concrete LLM provider (groq, gemini, nvidia, openrouter)."""

    #: Short name used in config and logs — must match an entry in LLM_PROVIDER_ORDER.
    name: str = "override-me"

    @abc.abstractmethod
    def enabled(self) -> bool:
        """True iff the API key for this provider is set and not TODO_pending."""

    @abc.abstractmethod
    def model_for(self, role: str) -> str:
        """`role` is 'draft' or 'score'; returns the configured model id."""

    @abc.abstractmethod
    async def draft(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.7,
        max_tokens: int = 1600,
    ) -> str:
        """Return a free-form text completion. Raises ProviderError on failure."""

    @abc.abstractmethod
    async def score_json(
        self,
        system: str,
        user: str,
        schema: Type[T],
        *,
        temperature: float = 0.0,
        max_tokens: int = 1200,
    ) -> T:
        """Return a Pydantic-validated JSON object. Raises ProviderError on failure."""

    def info(self) -> dict[str, Any]:
        return {"provider": self.name,
                "draft_model": self.model_for("draft"),
                "score_model": self.model_for("score")}
