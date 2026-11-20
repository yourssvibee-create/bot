"""Medium Playwright adapter — session-only skeleton."""
from __future__ import annotations

from typing import Any

from shared.config import get

from ._base import PlaywrightAdapter


class MediumAdapter(PlaywrightAdapter):
    platform = "medium"
    home_url = "https://medium.com/"
    login_url = "https://medium.com/m/signin"
    logged_in_selector = "a[href='/me/stories']"

    async def _attempt_credential_login(self, page: Any) -> None:
        # Medium's signin funnels through Google/Facebook/email-magic-link; all
        # require captcha or an inbox round-trip. Human must do this once headed.
        _ = get("MEDIUM_USERNAME")
        _ = get("MEDIUM_PASSWORD")
        raise NotImplementedError("medium: email magic-link; human-in-the-loop")
