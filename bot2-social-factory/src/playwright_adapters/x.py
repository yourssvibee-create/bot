"""X (Twitter) Playwright adapter — fallback when v2 API not wired."""
from __future__ import annotations

from typing import Any

from shared.config import get

from ..base_adapter import AdapterError, PostContent, PostResult
from ._base import PlaywrightAdapter
from . import _post_helpers as ph


class XAdapter(PlaywrightAdapter):
    platform = "x"
    home_url = "https://x.com/home"
    login_url = "https://x.com/i/flow/login"
    logged_in_selector = "a[data-testid='AppTabBar_Home_Link']"
    PREFER_API = True

    async def _attempt_credential_login(self, page: Any) -> None:
        user = get("X_USERNAME", required=True)
        pwd = get("X_PASSWORD", required=True)
        try:
            await page.fill("input[autocomplete='username']", user)
            await page.click("button:has-text('Next')")
            await page.fill("input[type='password']", pwd)
            await page.click("button[data-testid='LoginForm_Login_Button']")
        except Exception as e:
            raise NotImplementedError(f"x: login selectors drifted ({e})")

    async def post(self, content: PostContent) -> PostResult:
        if self.dry_run:
            return PostResult(ok=True, platform=self.platform)
        if self._page is None:
            raise AdapterError("x: login() must run first")
        page = self._page
        # Spec: caption contains '---'-separated thread parts. Split, trim each.
        parts = [p.strip() for p in (content.caption or "").split("---") if p.strip()] or [""]
        try:
            await page.goto("https://x.com/compose/post", wait_until="domcontentloaded")
            await ph.pause()
            # SELECTOR_TODO: verify live UI 2026-11-20 — tweet composer
            await ph.safe_fill(page, "div[data-testid='tweetTextarea_0']", parts[0][:280])
            await ph.pause()
            for idx, extra in enumerate(parts[1:], start=1):
                # SELECTOR_TODO: verify live UI 2026-11-20 — "Add another post"
                await ph.safe_click(page, "button[data-testid='addButton']")
                await ph.pause()
                await ph.safe_fill(page, f"div[data-testid='tweetTextarea_{idx}']",
                                   extra[:280])
                await ph.pause()
            # SELECTOR_TODO: verify live UI 2026-11-20 — Post All button for threads
            await ph.safe_click(page,
                "button[data-testid='tweetButton'], button[data-testid='tweetButtonInline']")
            url = page.url
        except Exception as e:
            return PostResult(ok=False, platform=self.platform, error=str(e)[:300])
        return PostResult(ok=True, platform=self.platform, external_url=url)

    async def verify_posted(self, url: str) -> bool:
        return "x.com" in url or "twitter.com" in url
