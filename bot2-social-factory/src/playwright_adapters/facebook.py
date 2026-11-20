"""Facebook Playwright adapter — session-only skeleton.

Prefer Meta Graph API with META_ACCESS_TOKEN; Playwright is fallback only.
"""
from __future__ import annotations

from typing import Any

from shared.config import get

from ._base import PlaywrightAdapter


class FacebookAdapter(PlaywrightAdapter):
    platform = "facebook"
    home_url = "https://www.facebook.com/"
    login_url = "https://www.facebook.com/login/"
    logged_in_selector = "div[aria-label='Account controls and settings']"

    async def _attempt_credential_login(self, page: Any) -> None:
        email = get("FACEBOOK_EMAIL", required=True)
        pwd = get("FACEBOOK_PASSWORD", required=True)
        try:
            await page.fill("input[name='email']", email)
            await page.fill("input[name='pass']", pwd)
            await page.click("button[name='login']")
        except Exception as e:
            raise NotImplementedError(f"facebook: login selectors drifted ({e})")
