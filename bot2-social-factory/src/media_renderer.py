"""Media renderer for Bot 2.

Pexels is implemented (simplest API: free, keyless for public search up to
200 req/hour with a header-less key via query param, but we use the official
Authorization header). Flux Schnell (HF Inference) and Pixabay are stubs.

Returns a list of local file paths that adapters can upload.
"""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Literal

import httpx

from shared.config import get
from shared.logging import get_logger

log = get_logger("bot2.media")

Provider = Literal["pexels", "pixabay", "flux_schnell"]

_CACHE_DIR = Path(__file__).resolve().parents[1] / ".media-cache"


def _cache_path(url: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha1(url.encode()).hexdigest()[:16]
    ext = ".jpg" if ".jpg" in url or ".jpeg" in url else ".png"
    return _CACHE_DIR / f"{h}{ext}"


async def fetch_pexels(query: str, *, per_page: int = 3, orientation: str = "landscape") -> list[str]:
    """Return URLs for up to `per_page` Pexels photos matching `query`."""
    api_key = get("PEXELS_API_KEY")
    if not api_key:
        log.warning("pexels_no_key", extra={"query": query})
        return []
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(
            "https://api.pexels.com/v1/search",
            params={"query": query, "per_page": per_page, "orientation": orientation},
            headers={"Authorization": api_key},
        )
        r.raise_for_status()
        data = r.json()
    photos = data.get("photos", [])
    return [p["src"]["large"] for p in photos if "src" in p]


async def _download(url: str) -> str:
    p = _cache_path(url)
    if p.exists() and p.stat().st_size > 0:
        return str(p)
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
        p.write_bytes(r.content)
    return str(p)


async def render(query: str, *, provider: Provider = "pexels",
                 n: int = 1, dry_run: bool = False) -> list[str]:
    """High-level entry point. Returns local file paths."""
    if dry_run:
        log.info("media_dry_run", extra={"query": query, "provider": provider, "n": n})
        return []
    if provider == "pexels":
        urls = await fetch_pexels(query, per_page=n)
        return await asyncio.gather(*[_download(u) for u in urls]) if urls else []
    if provider == "pixabay":
        raise NotImplementedError("pixabay provider not yet implemented")
    if provider == "flux_schnell":
        raise NotImplementedError("flux_schnell (HF Inference) not yet implemented")
    raise ValueError(f"unknown provider: {provider}")
