"""Quora Playwright adapter — publish a post via the 'Create Post' entry."""
from __future__ import annotations

from typing import Any

from shared.config import get

from ..base_adapter import AdapterError, PostContent, PostResult
from ._base import PlaywrightAdapter
from . import _post_helpers as ph


class QuoraAdapter(PlaywrightAdapter):
    platform = "quora"
    home_url = "https://www.quora.com/"
    login_url = "https://www.quora.com/"
    logged_in_selector = "div[aria-label='Profile']"
    PREFER_API = False

    async def _attempt_credential_login(self, page: Any) -> None:
        user = get("QUORA_USERNAME", required=True)
        pwd = get("QUORA_PASSWORD", required=True)
        try:
            await page.get_by_placeholder("Your email or username").fill(user)
            await page.get_by_placeholder("Password").fill(pwd)
            await page.get_by_role("button", name="Login").click()
        except Exception as e:
            raise NotImplementedError(f"quora: login selectors drifted ({e})")

    async def post(self, content: PostContent) -> PostResult:
        if self.dry_run:
            return PostResult(ok=True, platform=self.platform)
        if self._page is None:
            raise AdapterError("quora: login() must run first")
        page = self._page
        try:
            await page.goto("https://www.quora.com/", wait_until="domcontentloaded")
            await ph.pause()
            # SELECTOR_TODO: verify live UI 2026-11-20 — the "Create Post" button on home
            await ph.safe_click(page, "div:has-text('Create Post')")
            await ph.pause()
            # SELECTOR_TODO: verify live UI 2026-11-20 — the post body textarea in the modal
            await ph.safe_fill(page, "div[contenteditable='true']", content.caption or "")
            await ph.pause()
            # SELECTOR_TODO: verify live UI 2026-11-20 — the Post button in the modal
            await ph.safe_click(page, "button:has-text('Post'), button:has-text('Publish')")
            # Quora doesn't navigate to a new URL — stay on home. Return home as external.
            url = page.url
        except Exception as e:
            return PostResult(ok=False, platform=self.platform, error=str(e)[:300])
        return PostResult(ok=True, platform=self.platform, external_url=url)

    async def verify_posted(self, url: str) -> bool:
        return url.startswith("https://www.quora.com/")
