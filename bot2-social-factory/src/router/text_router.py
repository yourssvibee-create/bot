"""Bot 2 text router.

Thin async wrapper over shared.llm.draft_attributed that adds:
  - named "pools" (hook / long_form / caption / default) with narrower provider
    subsets for content types that need specific voice characteristics
  - deterministic per-seed primary selection so the same post_id always picks
    the same provider (less "same-signature AI" tell across platforms)
  - a (text, provider_key) return so the Telegram digest can attribute each
    piece of content to its generator

The underlying router (shared/llm/router.py) handles the 429/5xx fallback
walk. `pool=` passes the subset to shared.llm; `seed=` rotates the enabled
subset so seed N always picks the same primary before fallback.
"""
from __future__ import annotations

from shared.llm import draft_attributed

# Content-type → provider subset. Order inside each list is the preferred
# fallback order *before* seed rotation applies.
#
# NOTE: `gemini` is intentionally excluded from text pools as of 2026-11-21.
# The Gemini API free tier is disabled for this project's Google Cloud account;
# until billing is enabled at https://aistudio.google.com/billing, every text
# call to gemini returns 403 PERMISSION_DENIED and burns ~1s of fallback
# latency. The provider class stays registered in shared.llm (so the image
# router can still try it) and can be re-added here by adding "gemini" back
# into any list below once billing is enabled.
POOLS: dict[str, list[str]] = {
    "hook":      ["groq"],
    "long_form": ["nvidia", "openrouter"],
    "caption":   ["groq", "nvidia", "openrouter"],
    "default":   ["groq", "nvidia", "openrouter"],
}


async def generate(
    prompt: str,
    *,
    seed: int,
    pool: str = "default",
    system: str = "",
    temperature: float = 0.7,
    max_tokens: int = 500,
) -> tuple[str, str]:
    """Generate text; return (text, provider_name).

    `pool` picks which provider subset is eligible for this content type.
    `seed` makes the primary provider deterministic — same seed, same primary.
    """
    providers = POOLS.get(pool, POOLS["default"])
    return await draft_attributed(
        system, prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        seed=seed,
        pool=providers,
    )
