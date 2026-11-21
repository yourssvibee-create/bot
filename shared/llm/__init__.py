"""Multi-provider LLM facade.

Public surface used by every bot:

    from shared.llm import draft, score_json, LLMExhaustedError

Both calls traverse `LLM_PROVIDER_ORDER` (default: groq,gemini,nvidia,openrouter)
and fall back on 429/5xx, empty outputs, or JSON-validation failures. Adding a
5th provider is a one-file addition under `shared/llm/providers/` + a line in
`PROVIDER_REGISTRY` in `router.py`.
"""
from .router import LLMExhaustedError, draft, draft_attributed, score_json

__all__ = ["draft", "draft_attributed", "score_json", "LLMExhaustedError"]
