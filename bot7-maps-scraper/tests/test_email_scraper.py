"""Unit tests for src.enrich.email_scraper.

Covers:
  - pure parsers (_apply_blocklist, _pick_best, _pattern_guess)
  - page-level extractor (_extract_from_page) against three fixture files
    via a real Playwright context + file:// goto.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("BOT_ID", "bot7")

_BOT_ROOT = Path(__file__).resolve().parents[1]
if str(_BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(_BOT_ROOT))

from src.enrich.email_scraper import (
    _apply_blocklist,
    _extract_from_page,
    _pattern_guess,
    _pick_best,
)

FIXTURES = Path(__file__).parent / "fixtures"


# ---------- pure-function tests ---------------------------------------------

def test_blocklist_drops_placeholders():
    out = _apply_blocklist([
        "info@test.com",
        "example@example.com",
        "your@email.com",
        "name@domain.com",
        "deadbeef0123456789@cdn.test",   # hash email
        "contact@wixpress.com",
    ])
    assert out == ["info@test.com"]


def test_pick_best_prefers_role_order():
    assert _pick_best(["sales@x.com", "info@x.com", "contact@x.com"]) == "info@x.com"
    assert _pick_best(["random@x.com"]) == "random@x.com"
    assert _pick_best([]) is None


def test_pattern_guess_whitelist():
    assert _pattern_guess("acme.com") == "info@acme.com"
    assert _pattern_guess("acme.co") == "info@acme.co"
    assert _pattern_guess("acme.io") == "info@acme.io"
    assert _pattern_guess("acme.xyz") is None
    assert _pattern_guess("") is None


# ---------- fixture-based page tests ----------------------------------------

@pytest.mark.asyncio
async def test_mailto_fixture_is_high():
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto((FIXTURES / "fixture_mailto.html").as_uri())
        email, tier = await _extract_from_page(page)
        await ctx.close()
        await browser.close()
    assert email == "info@test.com"
    assert tier == "high"


@pytest.mark.asyncio
async def test_regex_fixture_is_medium():
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto((FIXTURES / "fixture_regex.html").as_uri())
        email, tier = await _extract_from_page(page)
        await ctx.close()
        await browser.close()
    assert email == "contact@test.com"
    assert tier == "medium"


@pytest.mark.asyncio
async def test_empty_fixture_returns_nothing():
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto((FIXTURES / "fixture_empty.html").as_uri())
        email, tier = await _extract_from_page(page)
        await ctx.close()
        await browser.close()
    assert email is None
    assert tier == ""
    # Pattern-guess on a trusted TLD domain fills in for this case in the
    # real _scan_domain flow — verified separately in the pure-function test.
    assert _pattern_guess("someindep.com") == "info@someindep.com"
