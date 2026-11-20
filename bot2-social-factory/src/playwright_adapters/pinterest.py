"""Pinterest Playwright adapter — fallback when Pinterest API v5 not wired."""
from __future__ import annotations

from typing import Any

from shared.config import get

from ..base_adapter import AdapterError, PostContent, PostResult
from ._base import PlaywrightAdapter
from . import _post_helpers as ph


class PinterestAdapter(PlaywrightAdapter):
    platform = "pinterest"
    home_url = "https://www.pinterest.com/"
    login_url = "https://www.pinterest.com/login/"
    logged_in_selector = "div[data-test-id='header-profile']"
    PREFER_API = True

    async def _attempt_credential_login(self, page: Any) -> None:
        user = get("PINTEREST_USERNAME", required=True)
        pwd = get("PINTEREST_PASSWORD", required=True)
        try:
            await page.fill("input[name='id']", user)
            await page.fill("input[name='password']", pwd)
            await page.click("button[type='submit']")
        except Exception as e:
            raise NotImplementedError(f"pinterest: login selectors drifted ({e})")

    async def post(self, content: PostContent) -> PostResult:
        if self.dry_run:
            return PostResult(ok=True, platform=self.platform)
        if self._page is None:
            raise AdapterError("pinterest: login() must run first")
        if not content.media_urls:
            return PostResult(ok=False, platform=self.platform,
                              error="pinterest pins require an image")
        page = self._page
        title, _, body = (content.caption or "").partition("\n")
        try:
            await page.goto("https://www.pinterest.com/pin-creation-tool/",
                            wait_until="domcontentloaded")
            await ph.pause()
            # SELECTOR_TODO: verify live UI 2026-11-20 — upload input
            file_input = await page.query_selector("input[type='file']")
            if file_input is None:
                raise RuntimeError("pinterest: upload input not found")
            await file_input.set_input_files(content.media_urls[:1])
            await ph.pause(2.0)
            # SELECTOR_TODO: verify live UI 2026-11-20 — title + description
            await ph.safe_fill(page, "textarea[placeholder='Add a title']",
                               (title or "Ominvo")[:100])
            await ph.pause()
            await ph.safe_fill(page, "div[data-test-id='pin-draft-description'] div[contenteditable='true']",
                               (body or content.caption or "")[:500])
            await ph.pause()
            # SELECTOR_TODO: verify live UI 2026-11-20 — Publish button
            await ph.safe_click(page, "button[data-test-id='board-dropdown-save-button']")
            url = page.url
        except Exception as e:
            return PostResult(ok=False, platform=self.platform, error=str(e)[:300])
        return PostResult(ok=True, platform=self.platform, external_url=url)

    async def verify_posted(self, url: str) -> bool:
        return "pinterest.com" in url
