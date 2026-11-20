"""TiktokAPIAdapter stub — tiktok official API.

Raises APINotRegisteredError until tokens are populated in .env.
Router catches this and falls back to the Playwright sibling.
"""
from __future__ import annotations

from ._api_stub import UnregisteredAPIAdapter


class TiktokAPIAdapter(UnregisteredAPIAdapter):
    platform = "tiktok"
