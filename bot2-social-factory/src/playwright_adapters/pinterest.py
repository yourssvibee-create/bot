"""Pinterest Playwright adapter — session-only skeleton.

Prefer Pinterest API v5 with PINTEREST_ACCESS_TOKEN; Playwright fallback only.
"""
from __future__ import annotations

from typing import Any

from shared.config import get

from ._base import PlaywrightAdapter


class PinterestAdapter(PlaywrightAdapter):
    platform = "pinterest"
    home_url = "https://www.pinterest.com/"
    login_url = "https://www.pinterest.com/login/"
    logged_in_selector = "div[data-test-id='header-profile']"

    async def _attempt_credential_login(self, page: Any) -> None:
        user = get("PINTEREST_USERNAME", required=True)
        pwd = get("PINTEREST_PASSWORD", required=True)
        try:
            await page.fill("input[name='id']", user)
            await page.fill("input[name='password']", pwd)
            await page.click("button[type='submit']")
        except Exception as e:
            raise NotImplementedError(f"pinterest: login selectors drifted ({e})")
