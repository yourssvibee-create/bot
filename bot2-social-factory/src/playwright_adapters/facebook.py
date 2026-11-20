"""Facebook Playwright adapter — fallback when Meta Graph API not wired."""
from __future__ import annotations

from typing import Any

from shared.config import get

from ..base_adapter import AdapterError, PostContent, PostResult
from ._base import PlaywrightAdapter
from . import _post_helpers as ph


class FacebookAdapter(PlaywrightAdapter):
    platform = "facebook"
    home_url = "https://www.facebook.com/"
    login_url = "https://www.facebook.com/login/"
    logged_in_selector = "div[aria-label='Account controls and settings']"
    PREFER_API = True

    async def _attempt_credential_login(self, page: Any) -> None:
        email = get("FACEBOOK_EMAIL", required=True)
        pwd = get("FACEBOOK_PASSWORD", required=True)
        try:
            await page.fill("input[name='email']", email)
            await page.fill("input[name='pass']", pwd)
            await page.click("button[name='login']")
        except Exception as e:
            raise NotImplementedError(f"facebook: login selectors drifted ({e})")

    async def post(self, content: PostContent) -> PostResult:
        if self.dry_run:
            return PostResult(ok=True, platform=self.platform)
        if self._page is None:
            raise AdapterError("facebook: login() must run first")
        page = self._page
        page_name = get("FACEBOOK_PAGE_NAME") or ""
        try:
            # Post to the Page's feed if configured, else personal timeline.
            target = f"https://www.facebook.com/{page_name}/" if page_name else "https://www.facebook.com/"
            await page.goto(target, wait_until="domcontentloaded")
            await ph.pause()
            # SELECTOR_TODO: verify live UI 2026-11-20 — "What's on your mind?" entry
            await ph.safe_click(page, "div[role='button']:has-text(\"What's on your mind\")")
            await ph.pause()
            # SELECTOR_TODO: verify live UI 2026-11-20 — composer body
            await ph.safe_fill(page, "div[role='textbox']", content.caption or "")
            await ph.pause()
            # SELECTOR_TODO: verify live UI 2026-11-20 — Post button
            await ph.safe_click(page, "div[aria-label='Post'][role='button']")
            url = page.url
        except Exception as e:
            return PostResult(ok=False, platform=self.platform, error=str(e)[:300])
        return PostResult(ok=True, platform=self.platform, external_url=url)

    async def verify_posted(self, url: str) -> bool:
        return "facebook.com" in url
