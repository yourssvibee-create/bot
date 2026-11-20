"""Instagram Playwright adapter — fallback when Meta Graph API not wired."""
from __future__ import annotations

from typing import Any

from shared.config import get

from ..base_adapter import AdapterError, PostContent, PostResult
from ._base import PlaywrightAdapter
from . import _post_helpers as ph


class InstagramAdapter(PlaywrightAdapter):
    platform = "instagram"
    home_url = "https://www.instagram.com/"
    login_url = "https://www.instagram.com/accounts/login/"
    logged_in_selector = "a[href^='/accounts/edit/']"
    PREFER_API = True

    async def _attempt_credential_login(self, page: Any) -> None:
        user = get("INSTAGRAM_USERNAME", required=True)
        pwd = get("INSTAGRAM_PASSWORD", required=True)
        try:
            await page.fill("input[name='username']", user)
            await page.fill("input[name='password']", pwd)
            await page.click("button[type='submit']")
        except Exception as e:
            raise NotImplementedError(f"instagram: login selectors drifted ({e})")

    async def post(self, content: PostContent) -> PostResult:
        if self.dry_run:
            return PostResult(ok=True, platform=self.platform)
        if self._page is None:
            raise AdapterError("instagram: login() must run first")
        if not content.media_urls:
            return PostResult(ok=False, platform=self.platform,
                              error="instagram requires at least one image/video")
        page = self._page
        try:
            await page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
            await ph.pause()
            # SELECTOR_TODO: verify live UI 2026-11-20 — "Create" nav button
            await ph.safe_click(page, "svg[aria-label='New post']")
            await ph.pause()
            # SELECTOR_TODO: verify live UI 2026-11-20 — file chooser trigger
            async with page.expect_file_chooser() as fc_info:
                await ph.safe_click(page, "button:has-text('Select from computer')")
            file_chooser = await fc_info.value
            await file_chooser.set_files(content.media_urls)
            await ph.pause()
            # Skip crop/filters: Next → Next → write caption → Share
            # SELECTOR_TODO: verify live UI 2026-11-20
            await ph.safe_click(page, "button:has-text('Next'):nth-match(1)")
            await ph.pause()
            await ph.safe_click(page, "button:has-text('Next'):nth-match(1)")
            await ph.pause()
            # SELECTOR_TODO: verify live UI 2026-11-20 — caption textarea
            await ph.safe_fill(page, "textarea[aria-label='Write a caption…']",
                               content.caption or "")
            await ph.pause()
            # SELECTOR_TODO: verify live UI 2026-11-20
            await ph.safe_click(page, "button:has-text('Share')")
            url = page.url
        except Exception as e:
            return PostResult(ok=False, platform=self.platform, error=str(e)[:300])
        return PostResult(ok=True, platform=self.platform, external_url=url)

    async def verify_posted(self, url: str) -> bool:
        return "instagram.com" in url
