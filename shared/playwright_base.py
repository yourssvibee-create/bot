"""Stealth-configured Playwright launcher with per-account persistent context."""
from __future__ import annotations

import random
from pathlib import Path
from typing import Optional

from playwright.async_api import BrowserContext, async_playwright

_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36",
]

_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
window.chrome = window.chrome || { runtime: {} };
"""


def session_dir(bot: str, account: str) -> Path:
    base = Path.home() / ".bot_sessions" / bot / account
    base.mkdir(parents=True, exist_ok=True)
    return base


async def launch_context(
    bot: str,
    account: str,
    *,
    headless: bool = True,
    user_agent: Optional[str] = None,
    viewport: tuple[int, int] = (1366, 768),
) -> tuple[BrowserContext, object]:
    """Persistent-context launch. Returns (context, playwright_handle).

    Caller must `await context.close()` and `await pw.stop()`.
    """
    pw = await async_playwright().start()
    ua = user_agent or random.choice(_UAS)
    ctx = await pw.chromium.launch_persistent_context(
        user_data_dir=str(session_dir(bot, account)),
        headless=headless,
        user_agent=ua,
        viewport={"width": viewport[0], "height": viewport[1]},
        locale="en-US",
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    )
    await ctx.add_init_script(_STEALTH_JS)
    return ctx, pw
