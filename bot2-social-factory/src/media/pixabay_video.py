"""Pixabay video B-roll adapter.

Re-exports `fetch_pixabay_clip` from `src.video_pipeline` so new callers can
`from src.media.pixabay_video import fetch_pixabay_clip` without touching the
original. The implementation stays in video_pipeline.py next to the other
media helpers (ffmpeg, Pexels, placeholder) to avoid an extra import layer.

Also exports `fetch_broll_urls(query, ...)` — a URL-list variant for callers
that want to pick among several clips rather than take the first one.
"""
from __future__ import annotations

import httpx

from shared.config import get
from shared.logging import get_logger
from src.video_pipeline import fetch_pixabay_clip  # noqa: F401

log = get_logger("bot2.pixabay_video")


async def fetch_broll_urls(
    query: str,
    *,
    per_page: int = 20,
    min_duration: int = 3,
    max_duration: int = 60,
) -> list[str]:
    """Return a ranked list of Pixabay video MP4 URLs for `query`.

    Returns [] on no-key, no-results, or non-recoverable error — caller falls
    back to typography or solid-color placeholder.
    """
    key = get("PIXABAY_API_KEY")
    if not key:
        log.warning("pixabay_no_key", extra={"query": query})
        return []
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                "https://pixabay.com/api/videos/",
                params={
                    "key": key, "q": query, "video_type": "film",
                    "per_page": per_page, "safesearch": "true",
                },
            )
        r.raise_for_status()
        hits = r.json().get("hits", [])
    except Exception as e:
        log.warning("pixabay_err", extra={"query": query, "err": str(e)[:200]})
        return []
    urls: list[str] = []
    for h in hits:
        duration = h.get("duration", 0)
        if duration < min_duration or duration > max_duration:
            continue
        videos = h.get("videos") or {}
        for tier in ("large", "medium", "small"):
            v = videos.get(tier) or {}
            url = v.get("url")
            if url:
                urls.append(url)
                break
    if not urls:
        log.info("pixabay_no_results", extra={"query": query})
    return urls
