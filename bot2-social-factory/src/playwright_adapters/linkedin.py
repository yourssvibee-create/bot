"""LinkedIn Playwright adapter — session-only skeleton.

Prefer LinkedIn Share API with LINKEDIN_ACCESS_TOKEN once OAuth is approved.
"""
from __future__ import annotations

from typing import Any

from shared.config import get

from ._base import PlaywrightAdapter


class LinkedInAdapter(PlaywrightAdapter):
    platform = "linkedin"
    home_url = "https://www.linkedin.com/feed/"
    login_url = "https://www.linkedin.com/login"
    logged_in_selector = "a[data-control-name='identity_welcome_message']"

    async def _attempt_credential_login(self, page: Any) -> None:
        email = get("LINKEDIN_EMAIL", required=True)
        pwd = get("LINKEDIN_PASSWORD", required=True)
        try:
            await page.fill("input#username", email)
            await page.fill("input#password", pwd)
            await page.click("button[type='submit']")
        except Exception as e:
            raise NotImplementedError(f"linkedin: login selectors drifted ({e})")
