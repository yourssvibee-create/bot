"""Quora scraper: public reputation / review questions. Playwright, 3s polite."""
from __future__ import annotations

import asyncio
import hashlib
from typing import Any
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from shared.logging import get_logger
from shared.playwright_base import launch_context

from ..config import settings

log = get_logger("bot1.scraper.quora")

QUERIES_DEFAULT = [
    "how to improve online reviews small business",
    "best way to respond to negative reviews",
    "reputation management for small business",
    "how to get more customer reviews",
]


def _sid(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:24]


async def scrape(queries: list[str] | None = None) -> list[dict[str, Any]]:
    cfg = settings()
    qs = queries or QUERIES_DEFAULT
    out: list[dict[str, Any]] = []
    ctx, pw = await launch_context("bot1", "quora", headless=cfg.playwright_headless)
    try:
        page = await ctx.new_page()
        for q in qs:
            url = f"https://www.quora.com/search?q={quote_plus(q)}&type=question"
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await asyncio.sleep(cfg.polite_delay_quora)
                html = await page.content()
            except Exception as e:
                log.warning("quora_search_fail", extra={"q": q, "err": str(e)})
                continue
            soup = BeautifulSoup(html, "html.parser")
            # Quora question links look like /<slugified-question>
            for a in soup.select("a[href^='/'][href*='-']"):
                href = a.get("href") or ""
                text = a.get_text(" ", strip=True)
                if not text or len(text) < 15:
                    continue
                if "?" not in text and not text.endswith("?"):
                    continue
                qurl = f"https://www.quora.com{href.split('?')[0]}"
                out.append({
                    "source": "quora",
                    "source_id": _sid(qurl),
                    "url": qurl,
                    "author": None,
                    "title": text[:300],
                    "body": None,
                    "raw": {"query": q},
                })
                if len(out) >= cfg.max_signals_per_source:
                    return out
    finally:
        await ctx.close()
        await pw.stop()
    log.info("quora_done", extra={"count": len(out)})
    return out
