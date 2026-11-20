"""Medium Playwright adapter.

post() creates a new story at medium.com/new-story, fills title + body, hits
Publish → Publish now. Selectors marked SELECTOR_TODO must be verified in a
live headed run before the first production post.
"""
from __future__ import annotations

from typing import Any

from shared.config import get

from ..base_adapter import AdapterError, PostContent, PostResult
from ._base import PlaywrightAdapter
from . import _post_helpers as ph


class MediumAdapter(PlaywrightAdapter):
    platform = "medium"
    home_url = "https://medium.com/"
    login_url = "https://medium.com/m/signin"
    logged_in_selector = "a[href='/me/stories']"
    PREFER_API = False  # no public writes API

    async def _attempt_credential_login(self, page: Any) -> None:
        _ = get("MEDIUM_USERNAME")
        _ = get("MEDIUM_PASSWORD")
        raise NotImplementedError("medium: email magic-link; human-in-the-loop once")

    async def post(self, content: PostContent) -> PostResult:
        if self.dry_run:
            return PostResult(ok=True, platform=self.platform)
        if self._page is None:
            raise AdapterError("medium: login() must run first")
        page = self._page
        caption = content.caption or ""
        # Split caption: first line as title, remainder as body.
        title, _, body = caption.partition("\n")
        title = (title or "Ominvo — advice").strip()[:120]
        body = (body.strip() or caption).strip()

        try:
            await page.goto("https://medium.com/new-story", wait_until="domcontentloaded")
            await ph.pause()
            # SELECTOR_TODO: verify live UI 2026-11-20
            await ph.safe_fill(page, "h3[data-default-value='Title']", title)
            await ph.pause()
            # SELECTOR_TODO: verify live UI 2026-11-20
            await ph.safe_fill(page, "p[data-default-value='Tell your story…']", body)
            await ph.pause()
            # SELECTOR_TODO: verify live UI 2026-11-20
            await ph.safe_click(page, "button:has-text('Publish')")
            await ph.pause()
            # SELECTOR_TODO: verify live UI 2026-11-20
            await ph.safe_click(page, "button:has-text('Publish now')")
            url = await ph.read_url(page, min_contains="medium.com/p/")
        except Exception as e:
            return PostResult(ok=False, platform=self.platform, error=str(e)[:300])
        return PostResult(ok=True, platform=self.platform, external_url=url)

    async def verify_posted(self, url: str) -> bool:
        if self._page is None:
            return False
        await self._page.goto(url, wait_until="domcontentloaded")
        return self.platform in self._page.url
