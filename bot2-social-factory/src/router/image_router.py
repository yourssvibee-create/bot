"""Bot 2 image router.

Rotates image generation across Gemini Nano Banana, HuggingFace FLUX.1-schnell,
and NVIDIA FLUX.1-dev. Primary provider is picked deterministically by seed;
on rate-limit or error, walks the remaining pool.

Returns (png_bytes | None, provider_key). None means all providers failed —
callers should fall back to kinetic typography or a solid placeholder.
"""
from __future__ import annotations

import asyncio
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
    """Gemini native image generation (google-genai 1.x).

    1.x removed `types.ImageConfig` from the public surface and the native
    image models (gemini-2.5-flash-image aka Nano Banana) don't accept a
    `response_modalities` config — the model emits the image unconditionally.
    Aspect ratio is best requested inline in the prompt.
    """
    from google import genai
    key = get("GEMINI_API_KEY")
    if not key:
        raise ImageProviderError("GEMINI_API_KEY not set")
    client = genai.Client(api_key=key)
    prompt_with_aspect = (
        f"{prompt}\n\nOutput image aspect ratio: {aspect}"
        if aspect and aspect != "1:1" else prompt
    )
    try:
        # genai client is sync; push to a thread so we don't block the loop.
        resp = await asyncio.to_thread(
            client.models.generate_content,
            model=model,
            contents=[prompt_with_aspect],
        )
    except Exception as e:
        msg = str(e)
        if ("quota" in msg.lower() or "429" in msg
                or "RESOURCE_EXHAUSTED" in msg):
            raise ImageRateLimitError(msg)
        raise ImageProviderError(msg)

    candidates = getattr(resp, "candidates", None) or []
    for cand in candidates:
        parts = getattr(getattr(cand, "content", None), "parts", None) or []
        for part in parts:
            inline = getattr(part, "inline_data", None)
            if inline is not None and getattr(inline, "data", None):
                return inline.data
    raise ImageProviderError("gemini: no image data in response")


_HF_URLS = (
    "https://router.huggingface.co/hf-inference/models/{model}",
    "https://api-inference.huggingface.co/models/{model}",
)


async def _hf(model: str, prompt: str, aspect: str) -> bytes | None:
    """HuggingFace FLUX.

    Tries the new Inference Providers router first, falls back to the legacy
    serverless endpoint (some models still answer there). Retries once on
    503 "loading"; propagates 429 as ImageRateLimitError so the pool walks.
    """
    token = get("HUGGINGFACE_API_TOKEN")
    if not token:
        raise ImageProviderError("HUGGINGFACE_API_TOKEN not set")
    headers = {"Authorization": f"Bearer {token}",
               "Accept": "image/png,application/json",
               "Content-Type": "application/json"}
    body = {"inputs": prompt, "parameters": {"guidance_scale": 0.0}}
    last_err = "hf: no attempt made"
    async with httpx.AsyncClient(timeout=180) as c:
        for url_tpl in _HF_URLS:
            url = url_tpl.format(model=model)
            for attempt in range(2):
                try:
                    r = await c.post(url, headers=headers, json=body)
                except Exception as e:
                    last_err = f"hf transport {url}: {e}"
                    break
                if r.status_code == 429:
                    raise ImageRateLimitError(r.text[:200])
                if r.status_code == 503:
                    last_err = f"hf 503 {url}"
                    await asyncio.sleep(3 + attempt * 5)
                    continue
                if r.status_code == 404:
                    last_err = f"hf 404 {url}"
                    break
                if r.is_success and r.content and len(r.content) > 256:
                    ct = r.headers.get("content-type", "")
                    if "image" in ct or r.content[:4] in (b"\x89PNG", b"\xff\xd8\xff\xe0"):
                        return r.content
                    # JSON error wrapped in 200 — fall through
                    last_err = f"hf 200 non-image at {url}: {r.text[:200]}"
                    break
                last_err = f"hf {r.status_code} {url}: {r.text[:200]}"
                break
    raise ImageProviderError(last_err)


async def _nvidia(model: str, prompt: str, aspect: str) -> bytes | None:
    """NVIDIA NIM FLUX image endpoint."""
    api_key = get("NVIDIA_API_KEY")
    if not api_key:
        raise ImageProviderError("NVIDIA_API_KEY not set")
    body = {"prompt": prompt, "cfg_scale": 3.5, "steps": 4}
    if aspect and aspect != "1:1":
        body["aspect_ratio"] = aspect
    async with httpx.AsyncClient(timeout=180) as c:
        try:
            r = await c.post(
                f"https://ai.api.nvidia.com/v1/genai/{model}",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json=body,
            )
        except Exception as e:
            raise ImageProviderError(f"nvidia transport: {e}")
    if r.status_code == 429:
        raise ImageRateLimitError(r.text[:200])
    if not r.is_success:
        raise ImageProviderError(f"nvidia {r.status_code}: {r.text[:200]}")
    try:
        data = r.json()
    except Exception as e:
        raise ImageProviderError(f"nvidia non-json response: {e}")
    b64 = None
    if isinstance(data.get("artifacts"), list) and data["artifacts"]:
        b64 = data["artifacts"][0].get("base64")
    b64 = (b64 or data.get("image") or data.get("b64_json")
           or data.get("output"))
    if not b64:
        raise ImageProviderError(
            f"nvidia response missing image data: {str(data)[:200]}"
        )
    try:
        return base64.b64decode(b64)
    except Exception as e:
        raise ImageProviderError(f"nvidia b64 decode: {e}")
