"""Bot 2 image router.

Rotates image generation across Gemini Nano Banana, HuggingFace FLUX.1-schnell,
and NVIDIA FLUX.1-dev. Primary provider is picked deterministically by seed;
on rate-limit or error, walks the remaining pool.

Returns (png_bytes | None, provider_key). None means all providers failed —
callers should fall back to kinetic typography or a solid placeholder.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass

import httpx

from shared.config import get
from shared.logging import get_logger

log = get_logger("bot2.image_router")


class ImageRateLimitError(RuntimeError):
    pass


class ImageProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class ImgProvider:
    key: str
    model: str
    weight: int


IMG_POOL: list[ImgProvider] = [
    ImgProvider("gemini",      "gemini-2.5-flash-image",           3),
    ImgProvider("huggingface", "black-forest-labs/FLUX.1-schnell", 2),
    ImgProvider("nvidia",      "black-forest-labs/flux.1-dev",     2),
]


def _pick(seed: int) -> ImgProvider:
    expanded = [p for p in IMG_POOL for _ in range(p.weight)]
    return expanded[seed % len(expanded)]


async def generate_image(prompt: str, *, seed: int,
                         aspect: str = "1:1") -> tuple[bytes | None, str]:
    """Generate one image. Returns (bytes | None, provider_key)."""
    primary = _pick(seed)
    order = [primary] + [p for p in IMG_POOL if p.key != primary.key]
    for p in order:
        try:
            img = await _dispatch(p, prompt, aspect)
            if img:
                log.info("image_router_ok", extra={
                    "provider": p.key, "model": p.model, "seed": seed,
                })
                return img, p.key
        except ImageRateLimitError as e:
            log.warning("image_router_rate_limit", extra={
                "provider": p.key, "err": str(e)[:200],
            })
        except ImageProviderError as e:
            log.warning("image_router_error", extra={
                "provider": p.key, "err": str(e)[:200],
            })
    log.error("image_router_exhausted", extra={"seed": seed})
    return None, "none"


async def generate_carousel(prompts: list[str], *, seed: int,
                            aspect: str = "1:1") -> list[tuple[bytes, str]]:
    """One image per prompt. Seed increments per slide so providers vary within
    the carousel for a mixed aesthetic signature."""
    out: list[tuple[bytes, str]] = []
    for i, prompt in enumerate(prompts):
        img, provider = await generate_image(prompt, seed=seed + i, aspect=aspect)
        if img:
            out.append((img, provider))
    return out


async def _dispatch(p: ImgProvider, prompt: str, aspect: str) -> bytes | None:
    if p.key == "gemini":
        return await _gemini(p.model, prompt, aspect)
    if p.key == "huggingface":
        return await _hf(p.model, prompt, aspect)
    if p.key == "nvidia":
        return await _nvidia(p.model, prompt, aspect)
    raise ImageProviderError(f"unknown image provider {p.key}")


async def _gemini(model: str, prompt: str, aspect: str) -> bytes | None:
    from google import genai
    from google.genai import types
    key = get("GEMINI_API_KEY")
    if not key:
        raise ImageProviderError("GEMINI_API_KEY not set")
    client = genai.Client(api_key=key)
    try:
        resp = client.models.generate_content(
            model=model, contents=[prompt],
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(aspect_ratio=aspect),
            ),
        )
        for part in resp.candidates[0].content.parts:
            if part.inline_data and part.inline_data.data:
                return part.inline_data.data
    except Exception as e:
        msg = str(e)
        if "quota" in msg.lower() or "429" in msg or "RESOURCE_EXHAUSTED" in msg:
            raise ImageRateLimitError(msg)
        raise ImageProviderError(msg)
    return None


async def _hf(model: str, prompt: str, aspect: str) -> bytes | None:
    token = get("HUGGINGFACE_API_TOKEN")
    if not token:
        raise ImageProviderError("HUGGINGFACE_API_TOKEN not set")
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(
            f"https://api-inference.huggingface.co/models/{model}",
            headers={"Authorization": f"Bearer {token}"},
            json={"inputs": prompt},
        )
    if r.status_code == 429:
        raise ImageRateLimitError(r.text[:200])
    if r.status_code == 503:
        raise ImageProviderError("HF model loading, retry later")
    if not r.is_success:
        raise ImageProviderError(f"HF {r.status_code}: {r.text[:200]}")
    return r.content


async def _nvidia(model: str, prompt: str, aspect: str) -> bytes | None:
    api_key = get("NVIDIA_API_KEY")
    if not api_key:
        raise ImageProviderError("NVIDIA_API_KEY not set")
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(
            f"https://ai.api.nvidia.com/v1/genai/{model}",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={"prompt": prompt, "cfg_scale": 3.5, "steps": 4},
        )
    if r.status_code == 429:
        raise ImageRateLimitError(r.text[:200])
    if not r.is_success:
        raise ImageProviderError(f"NVIDIA image {r.status_code}: {r.text[:200]}")
    data = r.json()
    b64 = None
    if isinstance(data.get("artifacts"), list) and data["artifacts"]:
        b64 = data["artifacts"][0].get("base64")
    b64 = b64 or data.get("image") or data.get("b64_json")
    if not b64:
        raise ImageProviderError("NVIDIA image response missing data")
    return base64.b64decode(b64)
