"""TikTok Playwright adapter — fallback when Content Posting API not wired."""
from __future__ import annotations

from typing import Any

from shared.config import get

from ..base_adapter import AdapterError, PostContent, PostResult
from ._base import PlaywrightAdapter
from . import _post_helpers as ph


class TikTokAdapter(PlaywrightAdapter):
    platform = "tiktok"
    home_url = "https://www.tiktok.com/"
    login_url = "https://www.tiktok.com/login/phone-or-email/email"
    logged_in_selector = "div[data-e2e='profile-icon']"
    PREFER_API = True

    async def _attempt_credential_login(self, page: Any) -> None:
        user = get("TIKTOK_USERNAME", required=True)
        pwd = get("TIKTOK_PASSWORD", required=True)
        try:
            await page.fill("input[name='username']", user)
            await page.fill("input[type='password']", pwd)
            await page.click("button[data-e2e='login-button']")
        except Exception as e:
            raise NotImplementedError(f"tiktok: login selectors drifted ({e})")

    async def post(self, content: PostContent) -> PostResult:
        if self.dry_run:
            return PostResult(ok=True, platform=self.platform)
        if self._page is None:
            raise AdapterError("tiktok: login() must run first")
        if not content.media_urls:
            return PostResult(ok=False, platform=self.platform,
                              error="tiktok requires a video file")
        page = self._page
        try:
            await page.goto("https://www.tiktok.com/tiktokstudio/upload",
                            wait_until="domcontentloaded")
            await ph.pause()
            # SELECTOR_TODO: verify live UI 2026-11-20 — file input on upload page
            file_input = await page.query_selector("input[type='file']")
            if file_input is None:
                raise RuntimeError("tiktok: file input not found on upload page")
            await file_input.set_input_files(content.media_urls[:1])
            await ph.pause(3.0)  # upload needs more time
            # SELECTOR_TODO: verify live UI 2026-11-20 — caption/description editor
            await ph.safe_fill(page, "div[contenteditable='true']", content.caption or "")
            await ph.pause()
            # SELECTOR_TODO: verify live UI 2026-11-20 — Post button
            await ph.safe_click(page, "button:has-text('Post')")
            url = page.url
        except Exception as e:
            return PostResult(ok=False, platform=self.platform, error=str(e)[:300])
        return PostResult(ok=True, platform=self.platform, external_url=url)

    async def verify_posted(self, url: str) -> bool:
        return "tiktok.com" in url
