"""TikTok Playwright adapter — session-only skeleton.

Prefer TikTok Content Posting API (TIKTOK_CLIENT_KEY/SECRET flow) once approved.
"""
from __future__ import annotations

from typing import Any

from shared.config import get

from ._base import PlaywrightAdapter


class TikTokAdapter(PlaywrightAdapter):
    platform = "tiktok"
    home_url = "https://www.tiktok.com/"
    login_url = "https://www.tiktok.com/login/phone-or-email/email"
    logged_in_selector = "div[data-e2e='profile-icon']"

    async def _attempt_credential_login(self, page: Any) -> None:
        user = get("TIKTOK_USERNAME", required=True)
        pwd = get("TIKTOK_PASSWORD", required=True)
        try:
            await page.fill("input[name='username']", user)
            await page.fill("input[type='password']", pwd)
            await page.click("button[data-e2e='login-button']")
        except Exception as e:
            raise NotImplementedError(f"tiktok: login selectors drifted ({e})")
