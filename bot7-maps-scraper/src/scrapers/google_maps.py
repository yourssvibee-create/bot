"""Google Maps scraper (Playwright stealth + feed-in-place parse + micro-click detail).

Design:
1. Load the search URL, dismiss consent, scroll the left feed until item
   count stops growing. Scroll completion is driven by item-count plateau,
   not DOM-stagnation time.
2. Parse the entire feed HTML once via BeautifulSoup to get name / rating /
   review_count / category / tagline / maps_url. No per-card clicks yet.
3. MICRO-CLICK PHASE (hard-capped at 90s/segment): walk each kept card's
   DOM anchor, click it, wait briefly for the detail pane, pull street
   address (aria-label of button[data-item-id="address"]) + website
   (href on a[data-item-id="authority"] / a[data-value="Website"]).
   Bail gracefully when the budget runs out — keep whatever we got.

Target: ~4 min/segment (30s scrape + ~90s detail cap + margin).
"""
from __future__ import annotations

import asyncio
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

from bs4 import BeautifulSoup, Tag

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.logging import get_logger
from shared.playwright_base import launch_context

from ..segments import is_chain
from ..types import Business, Segment

log = get_logger("bot7.scrapers.gmaps")

_FEED_SELECTOR = 'div[role="feed"]'
_CARD_LINK_SELECTOR = 'a.hfpxzc'
_CONSENT_BUTTONS = [
    'button[aria-label*="Accept all"]',
    'button:has-text("Accept all")',
    'button:has-text("I agree")',
    'button:has-text("Reject all")',
]
_END_OF_LIST_TEXT = "You've reached the end of the list"

_DETAIL_PHASE_BUDGET_S = 90  # hard cap for the whole micro-click phase

# Rating like "4.5(123)" or "4.5 (1,234)".
_RATING_RE = re.compile(r"(\d(?:\.\d)?)\s*(?:\(|stars)")
_REVIEW_COUNT_RE = re.compile(r"\(([\d,]+)\)")
_PHONE_RE = re.compile(r"(?:\+?1[\s\-.]*)?\(?\d{3}\)?[\s\-.]\d{3}[\s\-.]\d{4}")


def _extract_domain(url: str) -> str:
    if not url:
        return ""
    m = re.match(r"https?://(?:www\.)?([^/]+)/?", url)
    return m.group(1).lower() if m else ""


async def _dismiss_consent(page) -> None:
    for sel in _CONSENT_BUTTONS:
        try:
            btn = page.locator(sel).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click(timeout=2000)
                await page.wait_for_timeout(500)
                return
        except Exception:
            pass


async def _scroll_feed(page, cap: int, *, max_rounds: int = 60) -> int:
    prev = 0
    plateau = 0
    for rnd in range(max_rounds):
        count = await page.locator(_CARD_LINK_SELECTOR).count()
        if count >= cap:
            log.info("gmaps.scroll cap_hit n=%s cap=%s rounds=%s", count, cap, rnd)
            return count
        try:
            html_tail = await page.locator(_FEED_SELECTOR).inner_text(timeout=1000)
            if _END_OF_LIST_TEXT in html_tail:
                log.info("gmaps.scroll end_of_list n=%s rounds=%s", count, rnd)
                return count
        except Exception:
            pass
        if count == prev:
            plateau += 1
            if plateau >= 2:
                log.info("gmaps.scroll plateau n=%s rounds=%s", count, rnd)
                return count
        else:
            plateau = 0
            prev = count
        try:
            await page.locator(_FEED_SELECTOR).evaluate(
                "el => el.scrollBy(0, el.scrollHeight)"
            )
        except Exception:
            break
        await page.wait_for_timeout(1500)
    return await page.locator(_CARD_LINK_SELECTOR).count()


def _card_root(anchor: Tag) -> Tag:
    gp = anchor.parent.parent if anchor.parent is not None else None
    if isinstance(gp, Tag):
        return gp
    return anchor.parent if isinstance(anchor.parent, Tag) else anchor


def _parse_rating_block(card: Tag) -> tuple[Optional[float], Optional[int]]:
    rating: Optional[float] = None
    review_count: Optional[int] = None
    for span in card.find_all("span", attrs={"role": "img"}):
        label = span.get("aria-label") or ""
        m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*stars", label)
        if m:
            try:
                rating = float(m.group(1))
            except ValueError:
                pass
            break
    text = card.get_text(" ", strip=True)
    m = _REVIEW_COUNT_RE.search(text)
    if m:
        try:
            review_count = int(m.group(1).replace(",", ""))
        except ValueError:
            pass
    if rating is None:
        m = _RATING_RE.search(text)
        if m:
            try:
                rating = float(m.group(1))
            except ValueError:
                pass
    return rating, review_count


def _split_category_and_tagline(card: Tag) -> tuple[str, str, str]:
    """Return (category, tagline, phone_if_inline). Tagline is the short
    description row the feed renders ("High-end New American meals..."),
    NOT a street address."""
    category = ""
    tagline = ""
    phone = ""
    rows = card.find_all("div", class_="W4Efsd")
    seen_texts: list[str] = []
    for row in rows:
        if row.find("div", class_="W4Efsd") is not None:
            continue
        txt = row.get_text(" · ", strip=True)
        if txt:
            seen_texts.append(txt)

    for txt in seen_texts:
        if not phone:
            m = _PHONE_RE.search(txt)
            if m:
                phone = m.group(0).strip()

    info_rows = [t for t in seen_texts if not re.search(r"\(\d", t) and "stars" not in t.lower()]
    if info_rows:
        parts = [p.strip() for p in info_rows[0].split("·") if p.strip()]
        if parts:
            category = parts[0]
    if len(info_rows) >= 2:
        tagline = info_rows[1]
        tagline = re.sub(r"·\s*(Open|Closed|Closes|Opens)\b.*$", "", tagline).strip(" ·")
    return category, tagline, phone


def _parse_feed(html: str, segment: Segment, cap: int) -> list[tuple[int, Business]]:
    """Return list of (dom_index, Business). dom_index matches the order of
    a.hfpxzc anchors live on the page, so we can locate the same card later."""
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.select(_CARD_LINK_SELECTOR)
    out: list[tuple[int, Business]] = []
    seen: set[str] = set()
    for i, a in enumerate(anchors):
        if len(out) >= cap:
            break
        name = (a.get("aria-label") or "").strip()
        if not name:
            card_name = a.find("div", class_="qBF1Pd")
            if card_name:
                name = card_name.get_text(strip=True)
        if not name:
            continue
        if is_chain(name, segment.vertical.chain_exclusions):
            continue
        card = _card_root(a)
        rating, review_count = _parse_rating_block(card)
        category, tagline, phone = _split_category_and_tagline(card)
        href = a.get("href") or ""
        gmaps_url = href if href.startswith("http") else f"https://www.google.com{href}"

        dedup = f"{name.lower()}|{tagline[:30].lower()}"
        if dedup in seen:
            continue
        seen.add(dedup)

        out.append(
            (
                i,
                Business(
                    name=name,
                    street_address="",      # filled in micro-click phase
                    tagline=tagline,
                    phone=phone,
                    website="",             # filled in micro-click phase
                    category=category,
                    rating=rating,
                    review_count=review_count,
                    google_maps_url=gmaps_url,
                    scraped_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
        )
    return out


async def _extract_detail_via_goto(page, place_url: str) -> tuple[str, str]:
    """Navigate directly to the place URL, read street + website.

    Click-dancing in the feed is unreliable (sticky panes, occluded
    anchors). A direct goto gives us a guaranteed-fresh detail pane each
    call.
    """
    street = ""
    website = ""
    try:
        await page.goto(place_url, wait_until="domcontentloaded", timeout=8000)
    except Exception:
        return street, website
    try:
        addr = page.locator('button[data-item-id="address"]').first
        await addr.wait_for(state="visible", timeout=2500)
        lbl = await addr.get_attribute("aria-label")
        if lbl:
            street = re.sub(r"^\s*Address:\s*", "", lbl).strip()
    except Exception:
        return street, website

    for sel in ('a[data-item-id="authority"]', 'a[data-value="Website"]'):
        try:
            w = page.locator(sel).first
            if await w.count() > 0:
                href = await w.get_attribute("href")
                if href:
                    website = href
                    break
        except Exception:
            pass
    return street, website


async def _micro_click_phase(page, rows: list[tuple[int, Business]]) -> int:
    """Walk each kept card, fetch street+website, enforce a 90s hard cap.

    Returns count of rows that got a street address."""
    filled = 0
    started = time.time()
    for pos, (_, biz) in enumerate(rows):
        elapsed = time.time() - started
        if elapsed >= _DETAIL_PHASE_BUDGET_S:
            log.info(
                "gmaps.detail budget_exhausted at=%s/%s filled=%s elapsed=%.1fs",
                pos, len(rows), filled, elapsed,
            )
            break
        if not biz.google_maps_url:
            continue
        street, website = await _extract_detail_via_goto(page, biz.google_maps_url)
        if street:
            biz.street_address = street
            filled += 1
        if website:
            biz.website = website
            biz.domain = _extract_domain(website)
    log.info(
        "gmaps.detail done filled=%s/%s elapsed=%.1fs",
        filled, len(rows), time.time() - started,
    )
    return filled


async def scrape_segment(
    segment: Segment, *, cap: int = 120, headless: bool = True
) -> list[Business]:
    query = segment.query
    log.info("gmaps.scrape start seg=%s cap=%s headless=%s query=%r",
             segment.slug, cap, headless, query)

    url = f"https://www.google.com/maps/search/{quote_plus(query)}"
    ctx, pw = await launch_context(
        "bot7", f"gmaps_{segment.metro.slug}", headless=headless
    )
    try:
        page = await ctx.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            log.warning("gmaps.scrape goto_fail seg=%s err=%s", segment.slug, e)
            return []
        await page.wait_for_timeout(2000)
        await _dismiss_consent(page)

        try:
            await page.wait_for_selector(_FEED_SELECTOR, timeout=15000)
        except Exception:
            log.warning("gmaps.scrape no_feed seg=%s", segment.slug)
            return []

        count = await _scroll_feed(page, cap)
        log.info("gmaps.scrape scrolled seg=%s count=%s", segment.slug, count)

        html = await page.content()
        parsed = _parse_feed(html, segment, cap)
        log.info("gmaps.scrape parsed seg=%s kept=%s", segment.slug, len(parsed))

        await _micro_click_phase(page, parsed)

        rows = [b for _, b in parsed]
        log.info("gmaps.scrape done seg=%s kept=%s", segment.slug, len(rows))
        return rows
    finally:
        try:
            await ctx.close()
        except Exception:
            pass
        try:
            await pw.stop()
        except Exception:
            pass


def scrape_segment_sync(
    segment: Segment, *, cap: int = 120, headless: bool = True
) -> list[Business]:
    return asyncio.run(scrape_segment(segment, cap=cap, headless=headless))
