"""Shared machinery for API adapters whose tokens aren't provisioned yet.

Each un-provisioned platform exposes a subclass that raises
APINotRegisteredError on login(). Router catches that and falls to the
Playwright adapter when PREFER_API would otherwise pick the API path.
"""
from __future__ import annotations

from ..base_adapter import AdapterError, BasePlatformAdapter, PostContent, PostResult


class APINotRegisteredError(AdapterError):
    """Raised when a platform's API adapter has no tokens in .env yet."""


class UnregisteredAPIAdapter(BasePlatformAdapter):
    """Default base: every method raises APINotRegisteredError so the router
    falls back gracefully to the Playwright sibling."""
    method = "api"

    async def login(self) -> None:
        raise APINotRegisteredError(
            f"Register {self.platform} API and set tokens in .env"
        )

    async def post(self, content: PostContent) -> PostResult:
        raise APINotRegisteredError(
            f"Register {self.platform} API and set tokens in .env"
        )

    async def verify_posted(self, url: str) -> bool:
        return False
