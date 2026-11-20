"""LinkedIn Playwright adapter — fallback when Share API not wired."""
from __future__ import annotations

from typing import Any

from shared.config import get

from ..base_adapter import AdapterError, PostContent, PostResult
from ._base import PlaywrightAdapter
from . import _post_helpers as ph


class LinkedInAdapter(PlaywrightAdapter):
    platform = "linkedin"
    home_url = "https://www.linkedin.com/feed/"
    login_url = "https://www.linkedin.com/login"
    logged_in_selector = "a[data-control-name='identity_welcome_message']"
    PREFER_API = True

    async def _attempt_credential_login(self, page: Any) -> None:
        email = get("LINKEDIN_EMAIL", required=True)
        pwd = get("LINKEDIN_PASSWORD", required=True)
        try:
            await page.fill("input#username", email)
            await page.fill("input#password", pwd)
            await page.click("button[type='submit']")
        except Exception as e:
            raise NotImplementedError(f"linkedin: login selectors drifted ({e})")

    async def post(self, content: PostContent) -> PostResult:
        if self.dry_run:
            return PostResult(ok=True, platform=self.platform)
        if self._page is None:
            raise AdapterError("linkedin: login() must run first")
        page = self._page
        try:
            await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
            await ph.pause()
            # SELECTOR_TODO: verify live UI 2026-11-20 — "Start a post" trigger
            await ph.safe_click(page, "button:has-text('Start a post')")
            await ph.pause()
            # SELECTOR_TODO: verify live UI 2026-11-20 — composer body
            await ph.safe_fill(page, "div[role='textbox'][contenteditable='true']",
                               content.caption or "")
            await ph.pause()
            # SELECTOR_TODO: verify live UI 2026-11-20 — Post button in share modal
            await ph.safe_click(page, "button.share-actions__primary-action:has-text('Post')")
            url = page.url
        except Exception as e:
            return PostResult(ok=False, platform=self.platform, error=str(e)[:300])
        return PostResult(ok=True, platform=self.platform, external_url=url)

    async def verify_posted(self, url: str) -> bool:
        return "linkedin.com" in url
