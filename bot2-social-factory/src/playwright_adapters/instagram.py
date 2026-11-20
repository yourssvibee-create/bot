"""Instagram Playwright adapter — session-only skeleton.

Prefer Meta Graph API when META_ACCESS_TOKEN is available; Playwright is the
fallback for accounts without a connected FB Page.
"""
from __future__ import annotations

from typing import Any

from shared.config import get

from ._base import PlaywrightAdapter


class InstagramAdapter(PlaywrightAdapter):
    platform = "instagram"
    home_url = "https://www.instagram.com/"
    login_url = "https://www.instagram.com/accounts/login/"
    logged_in_selector = "a[href^='/accounts/edit/']"

    async def _attempt_credential_login(self, page: Any) -> None:
        user = get("INSTAGRAM_USERNAME", required=True)
        pwd = get("INSTAGRAM_PASSWORD", required=True)
        try:
            await page.fill("input[name='username']", user)
            await page.fill("input[name='password']", pwd)
            await page.click("button[type='submit']")
        except Exception as e:
            raise NotImplementedError(f"instagram: login selectors drifted ({e})")
