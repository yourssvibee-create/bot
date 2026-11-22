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
    log.info("quora_seed_queries", extra={"count": len(qs), "queries": qs})
    out: list[dict[str, Any]] = []
    errors: list[str] = []
    ctx, pw = await launch_context("bot1", "quora", headless=cfg.playwright_headless)
    try:
        page = await ctx.new_page()
        for q in qs:
            url = f"https://www.quora.com/search?q={quote_plus(q)}&type=question"
            log.info("quora_search_fetch", extra={"q": q, "url": url})
            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await asyncio.sleep(cfg.polite_delay_quora)
                html = await page.content()
                log.info("quora_search_fetched",
                         extra={"q": q,
                                "status": resp.status if resp else None,
                                "bytes": len(html)})
            except Exception as e:
                log.warning("quora_search_fail", extra={"q": q, "err": str(e)})
                errors.append(f"q={q!r}: {e}")
                continue
            soup = BeautifulSoup(html, "html.parser")
            # Quora question links look like /<slugified-question>
            before = len(out)
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
            log.info("quora_search_parsed",
                     extra={"q": q, "new_items": len(out) - before})
    finally:
        await ctx.close()
        await pw.stop()
    log.info("quora_done", extra={"count": len(out), "errors": len(errors)})
    if not out and errors:
        raise RuntimeError(
            f"quora: 0 signals across {len(qs)} queries; "
            f"{len(errors)} errors. First: {errors[0]}"
        )
    return out
