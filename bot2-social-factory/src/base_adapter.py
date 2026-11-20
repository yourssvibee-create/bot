"""Platform adapter contract shared by Bot 2's API and Playwright adapters.

Every platform (Bluesky, X, LinkedIn, Quora, …) exposes a subclass implementing
`login`, `post`, `verify_posted`. The router in `platform_router.py` doesn't
care whether a given adapter talks an HTTP API or drives a Chromium page.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class PostContent:
    """Normalised payload the router hands to every adapter."""
    content_type: str                      # reel | carousel | long | tweet | long_form_video
    caption: str                           # plain-text body (adapter may trim/wrap)
    media_urls: list[str]                  # http(s) URLs or local paths — adapter decides
    extra: dict[str, Any] | None = None    # platform-specific overrides (e.g. thread splits)


@dataclass
class PostResult:
    """Return value from `post(...)`."""
    ok: bool
    platform: str
    external_url: Optional[str] = None     # permalink, when the adapter can derive one
    error: Optional[str] = None            # short failure summary; full trace in logs


class AdapterError(RuntimeError):
    """Raised when the adapter cannot continue (auth lost, platform error, etc.)."""


class BasePlatformAdapter(abc.ABC):
    """Abstract base for every Bot 2 platform adapter."""

    #: short, lowercase slug used in logs and dispatch rows (e.g. "bluesky")
    platform: str = ""
    #: "api" or "playwright" — matches bot2_dispatches.method
    method: str = ""

    def __init__(self, *, dry_run: bool = False) -> None:
        self.dry_run = dry_run

    @abc.abstractmethod
    async def login(self) -> None:
        """Authenticate (API session or persisted browser state)."""

    @abc.abstractmethod
    async def post(self, content: PostContent) -> PostResult:
        """Publish one piece of content."""

    @abc.abstractmethod
    async def verify_posted(self, url: str) -> bool:
        """Confirm a published post is still live (used by `bot2_account_health`)."""

    async def close(self) -> None:
        """Release resources (close browser, async client). Default: no-op."""
        return None
