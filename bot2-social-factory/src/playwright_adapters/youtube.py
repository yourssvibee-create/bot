"""YouTube Playwright adapter — Shorts upload via studio.youtube.com."""
from __future__ import annotations

from typing import Any

from shared.config import get

from ..base_adapter import AdapterError, PostContent, PostResult
from ._base import PlaywrightAdapter
from . import _post_helpers as ph


class YouTubeAdapter(PlaywrightAdapter):
    platform = "youtube"
    home_url = "https://studio.youtube.com/"
    login_url = "https://accounts.google.com/signin"
    logged_in_selector = "ytcp-navigation-drawer"
    PREFER_API = True

    async def _attempt_credential_login(self, page: Any) -> None:
        _ = get("YOUTUBE_CHANNEL_NAME")
        raise NotImplementedError("youtube: Google signin requires 2FA; human-in-the-loop")

    async def post(self, content: PostContent) -> PostResult:
        if self.dry_run:
            return PostResult(ok=True, platform=self.platform)
        if self._page is None:
            raise AdapterError("youtube: login() must run first")
        if not content.media_urls:
            return PostResult(ok=False, platform=self.platform,
                              error="youtube requires a video file")
        page = self._page
        try:
            await page.goto("https://studio.youtube.com/", wait_until="domcontentloaded")
            await ph.pause()
            # SELECTOR_TODO: verify live UI 2026-11-20 — "Create" button
            await ph.safe_click(page, "ytcp-button#create-icon")
            await ph.pause()
            # SELECTOR_TODO: verify live UI 2026-11-20 — "Upload videos" menu item
            await ph.safe_click(page, "tp-yt-paper-item:has-text('Upload videos')")
            await ph.pause()
            # SELECTOR_TODO: verify live UI 2026-11-20 — file input
            file_input = await page.query_selector("input[type='file']")
            if file_input is None:
                raise RuntimeError("youtube: upload file input not found")
            await file_input.set_input_files(content.media_urls[:1])
            await ph.pause(4.0)
            # SELECTOR_TODO: verify live UI 2026-11-20 — title / description editors
            title, _, body = (content.caption or "").partition("\n")
            await ph.safe_fill(page, "ytcp-social-suggestions-textbox[label='Title'] div[contenteditable='true']",
                               title[:100] or "Ominvo Short")
            await ph.pause()
            await ph.safe_fill(page, "ytcp-social-suggestions-textbox[label='Description'] div[contenteditable='true']",
                               body or content.caption or "")
            await ph.pause()
            # Click Next × 3 to skip Elements/Checks/Visibility, then Publish
            for _ in range(3):
                await ph.safe_click(page, "ytcp-button#next-button")
                await ph.pause()
            # SELECTOR_TODO: verify live UI 2026-11-20 — choose Public before publish
            await ph.safe_click(page, "tp-yt-paper-radio-button[name='PUBLIC']")
            await ph.pause()
            await ph.safe_click(page, "ytcp-button#done-button")
            url = page.url
        except Exception as e:
            return PostResult(ok=False, platform=self.platform, error=str(e)[:300])
        return PostResult(ok=True, platform=self.platform, external_url=url)

    async def verify_posted(self, url: str) -> bool:
        return "youtube.com" in url
