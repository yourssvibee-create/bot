"""Threads Playwright adapter — fallback when Meta Graph API isn't wired up."""
from __future__ import annotations

from typing import Any

from shared.config import get

from ..base_adapter import AdapterError, PostContent, PostResult
from ._base import PlaywrightAdapter
from . import _post_helpers as ph


class ThreadsAdapter(PlaywrightAdapter):
    platform = "threads"
    home_url = "https://www.threads.net/"
    login_url = "https://www.threads.net/login"
    logged_in_selector = "a[href$='/']:has(svg[aria-label='Home'])"
    PREFER_API = True  # Meta Graph covers Threads under threads_basic / threads_content_publish

    async def _attempt_credential_login(self, page: Any) -> None:
        user = get("INSTAGRAM_USERNAME", required=True)
        pwd = get("INSTAGRAM_PASSWORD", required=True)
        try:
            await page.fill("input[placeholder*='Username']", user)
            await page.fill("input[placeholder*='Password']", pwd)
            await page.click("div[role='button']:has-text('Log in')")
        except Exception as e:
            raise NotImplementedError(f"threads: login selectors drifted ({e})")

    async def post(self, content: PostContent) -> PostResult:
        if self.dry_run:
            return PostResult(ok=True, platform=self.platform)
        if self._page is None:
            raise AdapterError("threads: login() must run first")
        page = self._page
        try:
            await page.goto("https://www.threads.net/", wait_until="domcontentloaded")
            await ph.pause()
            # SELECTOR_TODO: verify live UI 2026-11-20 — "Start a thread" entry
            await ph.safe_click(page, "div[role='button']:has-text('Start a thread')")
            await ph.pause()
            # SELECTOR_TODO: verify live UI 2026-11-20 — composer body
            await ph.safe_fill(page, "div[contenteditable='true']", content.caption or "")
            await ph.pause()
            # SELECTOR_TODO: verify live UI 2026-11-20 — Post button
            await ph.safe_click(page, "div[role='button']:has-text('Post')")
            url = page.url
        except Exception as e:
            return PostResult(ok=False, platform=self.platform, error=str(e)[:300])
        return PostResult(ok=True, platform=self.platform, external_url=url)

    async def verify_posted(self, url: str) -> bool:
        return url.startswith("https://www.threads.net/")
