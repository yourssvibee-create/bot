"""Trustpilot 1-3 star complaint scraper (Playwright, polite 1 req / 4s)."""
from __future__ import annotations

import asyncio
import hashlib
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from shared.logging import get_logger
from shared.playwright_base import launch_context

from ..config import settings

log = get_logger("bot1.scraper.trustpilot")

CATEGORY_URLS_DEFAULT = [
    "https://www.trustpilot.com/categories/software_company?stars=1&stars=2&stars=3",
    "https://www.trustpilot.com/categories/marketing_agency?stars=1&stars=2&stars=3",
    "https://www.trustpilot.com/categories/electronics_technology?stars=1&stars=2&stars=3",
]


def _sid(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:24]


async def scrape(category_urls: list[str] | None = None) -> list[dict[str, Any]]:
    cfg = settings()
    urls = category_urls or CATEGORY_URLS_DEFAULT
    log.info("tp_seed_urls", extra={"count": len(urls), "urls": urls})
    out: list[dict[str, Any]] = []
    errors: list[str] = []
    ctx, pw = await launch_context("bot1", "trustpilot", headless=cfg.playwright_headless)
    try:
        page = await ctx.new_page()
        for cat_url in urls:
            log.info("tp_cat_fetch", extra={"url": cat_url})
            try:
                resp = await page.goto(cat_url, wait_until="domcontentloaded", timeout=30_000)
                html = await page.content()
                status = resp.status if resp else None
                log.info("tp_cat_fetched",
                         extra={"url": cat_url, "status": status, "bytes": len(html)})
            except Exception as e:
                log.warning("tp_cat_fail", extra={"url": cat_url, "err": str(e)})
                errors.append(f"cat {cat_url}: {e}")
                continue
            soup = BeautifulSoup(html, "html.parser")
            cards = soup.select("a[href*='/review/']")
            log.info("tp_cat_parsed", extra={"url": cat_url, "cards": len(cards)})
            seen: set[str] = set()
            for a in cards:
                href = a.get("href")
                if not href or href in seen:
                    continue
                seen.add(href)
                biz_url = urljoin("https://www.trustpilot.com", href)
                await asyncio.sleep(cfg.polite_delay_trustpilot)
                try:
                    resp = await page.goto(biz_url + "?stars=1&stars=2&stars=3",
                                           wait_until="domcontentloaded", timeout=30_000)
                    biz_html = await page.content()
                    log.info("tp_biz_fetched",
                             extra={"url": biz_url,
                                    "status": resp.status if resp else None,
                                    "bytes": len(biz_html)})
                except Exception as e:
                    log.warning("tp_biz_fail", extra={"url": biz_url, "err": str(e)})
                    errors.append(f"biz {biz_url}: {e}")
                    continue
                bsoup = BeautifulSoup(biz_html, "html.parser")
                reviews = bsoup.select("article")
                for rv in reviews[:5]:
                    title_el = rv.select_one("h2, [data-service-review-title-typography]")
                    body_el = rv.select_one("p, [data-service-review-text-typography]")
                    author_el = rv.select_one("[data-consumer-name-typography]")
                    title = (title_el.get_text(strip=True) if title_el else "")[:300]
                    body = (body_el.get_text(" ", strip=True) if body_el else "")[:2000]
                    author = (author_el.get_text(strip=True) if author_el else "")[:120]
                    if not (title or body):
                        continue
                    sig_url = biz_url
                    out.append({
                        "source": "trustpilot",
                        "source_id": _sid(sig_url + "|" + title + "|" + author),
                        "url": sig_url,
                        "author": author or None,
                        "title": title or None,
                        "body": body or None,
                        "raw": {"category_url": cat_url},
                    })
                    if len(out) >= cfg.max_signals_per_source:
                        return out
    finally:
        await ctx.close()
        await pw.stop()
    log.info("tp_done", extra={"count": len(out), "errors": len(errors)})
    if not out and errors:
        raise RuntimeError(
            f"trustpilot: 0 signals across {len(urls)} category URLs; "
            f"{len(errors)} errors. First: {errors[0]}"
        )
    return out
