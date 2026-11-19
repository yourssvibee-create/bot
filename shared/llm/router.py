"""Ordered fallback router across LLM providers.

Reads `LLM_PROVIDER_ORDER` (default: groq,gemini,nvidia,openrouter), filters to
providers whose API key is set and not `TODO_pending`/empty, and attempts each
in order. A retryable `ProviderError` (429 / 5xx / empty / invalid JSON) moves
to the next provider; non-retryable raises immediately; exhausting the chain
raises `LLMExhaustedError`.

Every attempt is logged as one JSON line with: provider, role, model,
latency_ms, status (ok|error), usage/err detail.
"""
from __future__ import annotations

import time
from typing import Any, Type, TypeVar

from pydantic import BaseModel

from ..config import current_bot_id_or_none, get
from ..logging import get_logger
from .base import Provider, ProviderError
from .providers.gemini import GeminiProvider
from .providers.groq import GroqProvider
from .providers.nvidia import NvidiaProvider
from .providers.openrouter import OpenRouterProvider

T = TypeVar("T", bound=BaseModel)
log = get_logger("llm.router")

PROVIDER_REGISTRY: dict[str, type[Provider]] = {
    "groq": GroqProvider,
    "gemini": GeminiProvider,
    "nvidia": NvidiaProvider,
    "openrouter": OpenRouterProvider,
}


class LLMExhaustedError(RuntimeError):
    """All providers in the chain failed. `.attempts` lists each failure."""

    def __init__(self, attempts: list[dict[str, Any]]) -> None:
        self.attempts = attempts
        summary = " -> ".join(f"{a['provider']}:{a.get('err','?')[:80]}" for a in attempts)
        super().__init__(f"all LLM providers exhausted: {summary}")


def _provider_order() -> list[Provider]:
    raw = get("LLM_PROVIDER_ORDER", "groq,gemini,nvidia,openrouter") or ""
    names = [n.strip() for n in raw.split(",") if n.strip()]
    out: list[Provider] = []
    for n in names:
        cls = PROVIDER_REGISTRY.get(n)
        if cls is None:
            log.warning("unknown_provider", extra={"name": n})
            continue
        p = cls()
        if not p.enabled():
            log.info("provider_disabled",
                     extra={"provider": p.name, "reason": "api_key_missing_or_placeholder"})
            continue
        out.append(p)
    return out


async def _call(role: str, func_name: str, *args, **kwargs):
    attempts: list[dict[str, Any]] = []
    providers = _provider_order()
    bot_id = current_bot_id_or_none() or "unknown"
    if not providers:
        raise LLMExhaustedError([{"provider": "none", "bot_id": bot_id,
                                  "err": "no providers enabled"}])

    for p in providers:
        model = p.model_for(role)
        t0 = time.perf_counter()
        base = {"bot_id": bot_id, "provider": p.name, "role": role, "model": model}
        try:
            result = await getattr(p, func_name)(*args, **kwargs)
            latency_ms = int((time.perf_counter() - t0) * 1000)
            log.info("llm_ok",
                     extra={**base, "latency_ms": latency_ms, "status": "ok"})
            return result
        except ProviderError as e:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            log.warning("llm_err",
                        extra={**base, "latency_ms": latency_ms, "status": "error",
                               "retryable": e.retryable,
                               "status_code": e.status_code, "err": str(e)[:300]})
            attempts.append({**base, "latency_ms": latency_ms,
                             "status_code": e.status_code, "err": str(e)})
            if not e.retryable:
                raise
            continue
        except Exception as e:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            log.warning("llm_unexpected",
                        extra={**base, "latency_ms": latency_ms, "status": "error",
                               "err": str(e)[:300]})
            attempts.append({**base, "latency_ms": latency_ms, "err": str(e)})
            continue

    raise LLMExhaustedError(attempts)


async def draft(system: str, user: str, *, temperature: float = 0.7,
                max_tokens: int = 1600) -> str:
    return await _call("draft", "draft", system, user,
                       temperature=temperature, max_tokens=max_tokens)


async def score_json(system: str, user: str, schema: Type[T], *,
                     temperature: float = 0.0, max_tokens: int = 1200) -> T:
    return await _call("score", "score_json", system, user, schema,
                       temperature=temperature, max_tokens=max_tokens)
