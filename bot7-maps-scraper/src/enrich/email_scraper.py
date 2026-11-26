"""DIY contact-page email extractor.

Replaces Hunter.io. Visits each business's domain (homepage + /contact + a
couple more) using the same Playwright context the scraper already has
open, pulls the best-looking email, and tags it with a confidence tier:

  high   = found via an <a href="mailto:..."> link
  medium = regex match in page text (no mailto)
  low    = pattern-guessed info@<domain> when nothing was found but the
           domain's TLD is in the trusted whitelist
  ""     = no email found; email is None

No SMTP verification — too slow, often blocked, and false-positives are
cheap here because the string lands straight in a CSV the buyer sanity-
checks themselves.
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path
from typing import Iterable

from playwright.async_api import BrowserContext

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.logging import get_logger

from ..config import settings

log = get_logger("bot7.enrich.email")


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_HASH_EMAIL_RE = re.compile(r"[a-f0-9]{16,}@")

_PATHS = ("/", "/contact", "/contact-us", "/about")

_TRUSTED_TLDS = (".com", ".co", ".io", ".net", ".org")

_ROLE_PRIORITY = (
    "info@",
    "contact@",
    "hello@",
    "sales@",
    "support@",
)

_BLOCKLIST_SUBSTR = (
    "example.",
    "your@",
    "name@",
    "youremail",
    "email@domain",
    "sentry.io",
    "wixpress",
    "godaddy",
    "squarespace",
    "wordpress",
    "cloudflare",
    "gravatar",
    "mailchimp",
    "w.org",
)


def _is_blocked(email: str) -> bool:
    e = email.lower()
    if _HASH_EMAIL_RE.search(e):
        return True
    return any(b in e for b in _BLOCKLIST_SUBSTR)


def _apply_blocklist(emails: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in emails:
        if not raw:
            continue
        e = raw.strip().lower()
        if not e or _is_blocked(e):
            continue
        if e in seen:
            continue
        seen.add(e)
        out.append(e)
    return out


def _pick_best(emails: list[str]) -> str | None:
    """Prefer role addresses in the documented order, else first match."""
    if not emails:
        return None
    for role in _ROLE_PRIORITY:
        for e in emails:
            if e.startswith(role):
                return e
    return emails[0]


def _pattern_guess(domain: str) -> str | None:
    """info@<domain> only when the TLD is in the trusted whitelist."""
    d = (domain or "").lower().strip()
    if not d:
        return None
    if not any(d.endswith(tld) for tld in _TRUSTED_TLDS):
        return None
    return f"info@{d}"


async def _extract_from_page(page) -> tuple[str | None, str]:
    """Scan the currently-loaded page. Returns (email, tier) or (None, '')."""
    try:
        anchors = await page.locator('a[href^="mailto:"]').all()
    except Exception:
        anchors = []
    mailto_emails: list[str] = []
    for a in anchors:
        try:
            href = await a.get_attribute("href")
        except Exception:
            href = None
        if not href:
            continue
        raw = href.split(":", 1)[1].split("?", 1)[0]
        mailto_emails.append(raw)
    cleaned = _apply_blocklist(mailto_emails)
    picked = _pick_best(cleaned)
    if picked:
        return picked, "high"

    try:
        text = await page.inner_text("body", timeout=1500)
    except Exception:
        text = ""
    matches = _EMAIL_RE.findall(text or "")
    cleaned = _apply_blocklist(matches)
    picked = _pick_best(cleaned)
    if picked:
        return picked, "medium"

    return None, ""


async def _scan_domain(
    context: BrowserContext, domain: str, timeout_s: int, max_paths: int
) -> tuple[str | None, str]:
    paths = _PATHS[:max_paths]
    page = await context.new_page()
    try:
        for p in paths:
            url = f"https://{domain}{p}"
            try:
                await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=timeout_s * 1000,
                )
            except Exception as e:
                log.debug("email.goto.fail domain=%s path=%s err=%s", domain, p, e)
                continue
            email, tier = await _extract_from_page(page)
            if email:
                return email, tier
        guessed = _pattern_guess(domain)
        if guessed:
            return guessed, "low"
        return None, ""
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def extract_emails(
    domains: list[str],
    context: BrowserContext,
) -> dict[str, tuple[str | None, str]]:
    """Scan each unique domain for a contact email. Fail-soft, concurrent."""
    uniq: list[str] = []
    seen: set[str] = set()
    for d in domains:
        if not d:
            continue
        k = d.lower().strip()
        if k in seen:
            continue
        seen.add(k)
        uniq.append(k)
    if not uniq:
        return {}

    sem = asyncio.Semaphore(settings.email_scrape_concurrency)
    timeout_s = settings.email_scrape_timeout_s
    max_paths = max(1, settings.email_scrape_max_paths)

    results: dict[str, tuple[str | None, str]] = {}

    async def _one(d: str) -> None:
        async with sem:
            try:
                results[d] = await _scan_domain(context, d, timeout_s, max_paths)
            except Exception as e:
                log.warning("email.scan.fail domain=%s err=%s", d, e)
                results[d] = (None, "")

    await asyncio.gather(*(_one(d) for d in uniq), return_exceptions=True)
    log.info(
        "email.batch domains=%s hit=%s",
        len(uniq),
        sum(1 for v in results.values() if v[0]),
    )
    return results


_TIER_TO_INT = {"high": 90, "medium": 60, "low": 30, "": None}


def tier_to_int(tier: str) -> int | None:
    """Map a confidence tier to the int the DB column expects."""
    return _TIER_TO_INT.get(tier, None)
