"""Quora scraper: public reputation / review questions. Playwright stealth."""
from __future__ import annotations

import asyncio
import hashlib
import random
import re
from typing import Any
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from shared.logging import get_logger
from shared.playwright_base import launch_context

from ..config import settings

log = get_logger("bot1.scraper.quora")

_RETRIES = 3
_JITTER_MIN = 2.0
_JITTER_MAX = 5.0

# Non-question Quora URL prefixes to drop from search-result anchors.
_SKIP_PREFIXES = (
    "/topic/", "/profile/", "/log/", "/subdomain/", "/q/", "/answer/",
    "/sitemap/", "/careers", "/about", "/help",
)

# Loose "looks like a question" heuristic — ends in ? OR starts with a
# WH / modal / "best"-style opener. Quora search mixes questions, spaces,
# and promoted content; this keeps questions and drops the chaff.
_Q_OPENER = re.compile(
    r"^\s*(how|what|why|when|where|who|which|can|could|should|would|is|are|does|do|did|will|best|any)\b",
    re.IGNORECASE,
)


def _looks_like_question(text: str) -> bool:
    t = text.strip()
    if len(t) < 15:
        return False
    if t.endswith("?"):
        return True
    return bool(_Q_OPENER.match(t))


async def _stealth_goto(page, url: str, *, label: str) -> tuple[int | None, str]:
    """Goto with jitter + retry on 403/5xx. Returns (status, html)."""
    last_status: int | None = None
    last_html = ""
    for attempt in range(1, _RETRIES + 1):
        await asyncio.sleep(random.uniform(_JITTER_MIN, _JITTER_MAX))
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            last_html = await page.content()
            last_status = resp.status if resp else None
            log.info(f"{label}_fetched",
                     extra={"url": url, "status": last_status,
                            "bytes": len(last_html), "attempt": attempt})
            if last_status is None or last_status < 400:
                return last_status, last_html
        except Exception as e:
            log.warning(f"{label}_err",
                        extra={"url": url, "err": str(e), "attempt": attempt})
    return last_status, last_html

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
            status, html = await _stealth_goto(page, url, label="quora_search")
            if status is None or status >= 400 or not html:
                errors.append(f"q={q!r}: status={status}")
                continue
            soup = BeautifulSoup(html, "html.parser")
            before = len(out)
            seen_hrefs: set[str] = set()
            # Loosened: any internal anchor whose text looks like a question
            # and whose href is a slug (contains '-') and isn't a known
            # non-question prefix. Markup churns, but this heuristic survives.
            for a in soup.select("a[href^='/']"):
                href = (a.get("href") or "").split("?")[0]
                if not href or href in seen_hrefs or "-" not in href:
                    continue
                if any(href.startswith(p) for p in _SKIP_PREFIXES):
                    continue
                text = a.get_text(" ", strip=True)
                if not _looks_like_question(text):
                    continue
                seen_hrefs.add(href)
                qurl = f"https://www.quora.com{href}"
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
        log.warning("quora_all_blocked",
                    extra={"errors": len(errors), "first": errors[0]})
    return out
