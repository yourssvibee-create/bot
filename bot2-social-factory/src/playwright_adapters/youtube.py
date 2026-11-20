"""YouTube Playwright adapter — session-only skeleton.

Prefer YouTube Data API v3 with YOUTUBE_CLIENT_ID/SECRET/REFRESH_TOKEN; use
Playwright only for Shorts uploads via Studio UI when the API quota is dry.
"""
from __future__ import annotations

from typing import Any

from shared.config import get

from ._base import PlaywrightAdapter


class YouTubeAdapter(PlaywrightAdapter):
    platform = "youtube"
    home_url = "https://studio.youtube.com/"
    login_url = "https://accounts.google.com/signin"
    logged_in_selector = "ytcp-navigation-drawer"

    async def _attempt_credential_login(self, page: Any) -> None:
        # Google auth now requires device-trust / 2FA. Human-in-the-loop once.
        _ = get("YOUTUBE_CHANNEL_NAME")
        raise NotImplementedError("youtube: Google signin requires 2FA; human-in-the-loop")
