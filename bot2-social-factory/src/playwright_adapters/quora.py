"""Quora Playwright adapter — session-only skeleton."""
from __future__ import annotations

from typing import Any

from shared.config import get

from ._base import PlaywrightAdapter


class QuoraAdapter(PlaywrightAdapter):
    platform = "quora"
    home_url = "https://www.quora.com/"
    login_url = "https://www.quora.com/"
    logged_in_selector = "div[aria-label='Profile']"

    async def _attempt_credential_login(self, page: Any) -> None:
        user = get("QUORA_USERNAME", required=True)
        pwd = get("QUORA_PASSWORD", required=True)
        # Quora shows an inline login modal on the landing page. Selectors are
        # unstable — best-effort fill, bail if we can't find them.
        try:
            await page.get_by_placeholder("Your email or username").fill(user)
            await page.get_by_placeholder("Password").fill(pwd)
            await page.get_by_role("button", name="Login").click()
        except Exception as e:
            raise NotImplementedError(f"quora: login selectors drifted ({e})")
