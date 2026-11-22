"""X (Twitter) scraper via Nitter — no API.

Rotates through instances per-query with a random starting offset, detects
dead-page responses (small body with no timeline-items), marks bad instances
dead for the remainder of the run, and falls through to the next one.
"""
from __future__ import annotations

import asyncio
import hashlib
import random
from typing import Any
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from shared.logging import get_logger

from ..config import nitter_instances, settings

log = get_logger("bot1.scraper.x")

# Responses smaller than this with 0 timeline items are the "cannot fetch
# from Twitter" / rate-limit placeholder page Nitter mirrors serve when the
# upstream API blocks them. Treat the instance as dead for this run.
_DEAD_PAGE_MAX_BYTES = 20_000

QUERIES_DEFAULT = [
    '"bad reviews" small business',
    '"negative reviews" help',
    '"need more reviews" small business',
    "reputation management",
]


def _sid(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:24]


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1.5, min=1, max=10))
async def _fetch(client: httpx.AsyncClient, url: str) -> tuple[int, str] | None:
    r = await client.get(url, timeout=20)
    log.info("x_fetch_resp",
             extra={"url": url, "status": r.status_code, "bytes": len(r.text)})
    if r.status_code >= 400:
        return None
    return (r.status_code, r.text)


async def scrape(queries: list[str] | None = None) -> list[dict[str, Any]]:
    cfg = settings()
    qs = queries or QUERIES_DEFAULT
    instances = nitter_instances()
    log.info("x_seed_queries",
             extra={"count": len(qs), "queries": qs,
                    "instances": instances})
    if not instances:
        log.warning("x_no_instances")
        return []

    # Per-run ring of live instances. Start at a random offset so we don't
    # hammer the same mirror first on every CI run.
    start = random.randrange(len(instances)) if instances else 0
    ring = instances[start:] + instances[:start]
    dead: set[str] = set()

    out: list[dict[str, Any]] = []
    errors: list[str] = []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ominvo-bot1/0.1)"}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        for q in qs:
            html: str | None = None
            items: list = []
            picked: str | None = None
            per_q_errors: list[str] = []
            for base in ring:
                if base in dead:
                    continue
                url = f"{base.rstrip('/')}/search?f=tweets&q={quote_plus(q)}"
                log.info("x_try_instance", extra={"q": q, "url": url})
                try:
                    res = await _fetch(client, url)
                except Exception as e:
                    per_q_errors.append(f"{base}: {e}")
                    log.warning("x_instance_err",
                                extra={"instance": base, "err": str(e)})
                    dead.add(base)
                    continue
                if not res:
                    dead.add(base)
                    continue
                _status, candidate_html = res
                candidate_soup = BeautifulSoup(candidate_html, "html.parser")
                candidate_items = candidate_soup.select(".timeline-item")
                # Dead-page signature: small body AND zero timeline items.
                if not candidate_items and len(candidate_html) < _DEAD_PAGE_MAX_BYTES:
                    log.warning("x_instance_dead_page",
                                extra={"instance": base,
                                       "bytes": len(candidate_html),
                                       "q": q})
                    dead.add(base)
                    continue
                html = candidate_html
                items = candidate_items
                picked = base
                break
            if html is None:
                log.warning("x_all_instances_failed",
                            extra={"q": q, "errors": per_q_errors,
                                   "dead_count": len(dead)})
                errors.append(f"q={q!r}: all instances dead")
                continue
            await asyncio.sleep(cfg.polite_delay_nitter)
            log.info("x_parsed",
                     extra={"q": q, "instance": picked, "items": len(items)})
            for item in items:
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
    log.info("x_done",
             extra={"count": len(out), "errors": len(errors),
                    "dead_instances": sorted(dead)})
    if not out and errors:
        log.warning("x_all_dead",
                    extra={"errors": len(errors),
                           "dead": sorted(dead)})
    return out
