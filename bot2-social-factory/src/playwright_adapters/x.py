"""X (Twitter) Playwright adapter — session-only skeleton.

Prefer X v2 API with X_API_KEY/SECRET/ACCESS tokens; Playwright fallback only.
"""
from __future__ import annotations

from typing import Any

from shared.config import get

from ._base import PlaywrightAdapter


class XAdapter(PlaywrightAdapter):
    platform = "x"
    home_url = "https://x.com/home"
    login_url = "https://x.com/i/flow/login"
    logged_in_selector = "a[data-testid='AppTabBar_Home_Link']"

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
