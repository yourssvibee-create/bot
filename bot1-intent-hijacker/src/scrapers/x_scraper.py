"""X (Twitter) scraper via Nitter — no API. Walks instance list until one works."""
from __future__ import annotations

import asyncio
import hashlib
from typing import Any
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from shared.logging import get_logger

from ..config import nitter_instances, settings

log = get_logger("bot1.scraper.x")

QUERIES_DEFAULT = [
    '"bad reviews" small business',
    '"negative reviews" help',
    '"need more reviews" small business',
    "reputation management",
]


def _sid(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:24]


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1.5, min=1, max=10))
async def _fetch(client: httpx.AsyncClient, url: str) -> str | None:
    r = await client.get(url, timeout=20)
    if r.status_code >= 400:
        return None
    return r.text


async def scrape(queries: list[str] | None = None) -> list[dict[str, Any]]:
    cfg = settings()
    qs = queries or QUERIES_DEFAULT
    instances = nitter_instances()
    if not instances:
        log.warning("x_no_instances")
        return []
    out: list[dict[str, Any]] = []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ominvo-bot1/0.1)"}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        for q in qs:
            html: str | None = None
            picked: str | None = None
            for base in instances:
                url = f"{base.rstrip('/')}/search?f=tweets&q={quote_plus(q)}"
                try:
                    html = await _fetch(client, url)
                except Exception:
                    html = None
                if html:
                    picked = base
                    break
            if not html:
                log.warning("x_all_instances_failed", extra={"q": q})
                continue
            await asyncio.sleep(cfg.polite_delay_nitter)
            soup = BeautifulSoup(html, "html.parser")
            for item in soup.select(".timeline-item"):
                link_el = item.select_one("a.tweet-link")
                body_el = item.select_one(".tweet-content")
                author_el = item.select_one(".username")
                if not link_el or not body_el:
                    continue
                href = link_el.get("href") or ""
                tweet_url = f"https://twitter.com{href}" if href.startswith("/") else href
                body = body_el.get_text(" ", strip=True)[:1500]
                author = author_el.get_text(strip=True) if author_el else None
                out.append({
                    "source": "x",
                    "source_id": _sid(tweet_url),
                    "url": tweet_url,
                    "author": author,
                    "title": None,
                    "body": body,
                    "raw": {"query": q, "instance": picked},
                })
                if len(out) >= cfg.max_signals_per_source:
                    return out
    log.info("x_done", extra={"count": len(out)})
    return out
