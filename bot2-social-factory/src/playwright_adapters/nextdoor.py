"""Nextdoor Playwright adapter — session-only skeleton."""
from __future__ import annotations

from typing import Any

from shared.config import get

from ._base import PlaywrightAdapter


class NextdoorAdapter(PlaywrightAdapter):
    platform = "nextdoor"
    home_url = "https://nextdoor.com/news_feed/"
    login_url = "https://nextdoor.com/login/"
    logged_in_selector = "a[href^='/profile/']"

    async def _attempt_credential_login(self, page: Any) -> None:
        email = get("NEXTDOOR_EMAIL", required=True)
        pwd = get("NEXTDOOR_PASSWORD", required=True)
        try:
            await page.fill("input[name='email']", email)
            await page.fill("input[name='password']", pwd)
            await page.click("button[type='submit']")
        except Exception as e:
            raise NotImplementedError(f"nextdoor: login selectors drifted ({e})")
