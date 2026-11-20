"""Threads (Meta) Playwright adapter — session-only skeleton.

Threads logs in via Instagram SSO; cookies cross over.
"""
from __future__ import annotations

from typing import Any

from shared.config import get

from ._base import PlaywrightAdapter


class ThreadsAdapter(PlaywrightAdapter):
    platform = "threads"
    home_url = "https://www.threads.net/"
    login_url = "https://www.threads.net/login"
    logged_in_selector = "a[href$='/']:has(svg[aria-label='Home'])"

    async def _attempt_credential_login(self, page: Any) -> None:
        user = get("INSTAGRAM_USERNAME", required=True)
        pwd = get("INSTAGRAM_PASSWORD", required=True)
        try:
            await page.fill("input[placeholder*='Username']", user)
            await page.fill("input[placeholder*='Password']", pwd)
            await page.click("div[role='button']:has-text('Log in')")
        except Exception as e:
            raise NotImplementedError(f"threads: login selectors drifted ({e})")
