"""Nextdoor Playwright adapter — publish to the user's neighborhood feed."""
from __future__ import annotations

from typing import Any

from shared.config import get

from ..base_adapter import AdapterError, PostContent, PostResult
from ._base import PlaywrightAdapter
from . import _post_helpers as ph


class NextdoorAdapter(PlaywrightAdapter):
    platform = "nextdoor"
    home_url = "https://nextdoor.com/news_feed/"
    login_url = "https://nextdoor.com/login/"
    logged_in_selector = "a[href^='/profile/']"
    PREFER_API = False

    async def _attempt_credential_login(self, page: Any) -> None:
        email = get("NEXTDOOR_EMAIL", required=True)
        pwd = get("NEXTDOOR_PASSWORD", required=True)
        try:
            await page.fill("input[name='email']", email)
            await page.fill("input[name='password']", pwd)
            await page.click("button[type='submit']")
        except Exception as e:
            raise NotImplementedError(f"nextdoor: login selectors drifted ({e})")

    async def post(self, content: PostContent) -> PostResult:
        if self.dry_run:
            return PostResult(ok=True, platform=self.platform)
        if self._page is None:
            raise AdapterError("nextdoor: login() must run first")
        page = self._page
        try:
            await page.goto("https://nextdoor.com/news_feed/", wait_until="domcontentloaded")
            await ph.pause()
            # SELECTOR_TODO: verify live UI 2026-11-20 — "Start a post" composer entry
            await ph.safe_click(page, "button[aria-label='Start a post']")
            await ph.pause()
            # SELECTOR_TODO: verify live UI 2026-11-20 — composer body
            await ph.safe_fill(page, "div[contenteditable='true']", content.caption or "")
            await ph.pause()
            # SELECTOR_TODO: verify live UI 2026-11-20 — Post button
            await ph.safe_click(page, "button:has-text('Post')")
            url = page.url
        except Exception as e:
            return PostResult(ok=False, platform=self.platform, error=str(e)[:300])
        return PostResult(ok=True, platform=self.platform, external_url=url)

    async def verify_posted(self, url: str) -> bool:
        return url.startswith("https://nextdoor.com/")
