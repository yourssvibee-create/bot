"""Bot 2 API adapters. Bluesky is fully working via atproto; the rest are
stubs that raise APINotRegisteredError until their tokens land in .env."""
from ._api_stub import APINotRegisteredError, UnregisteredAPIAdapter
from .bluesky import BlueskyAdapter
from .linkedin import LinkedinAPIAdapter
from .meta import MetaAdapter
from .pinterest import PinterestAPIAdapter
from .tiktok import TiktokAPIAdapter
from .x import XAPIAdapter
from .youtube import YoutubeAPIAdapter

# Router registry: maps platform slug to API-adapter class. Bluesky is the
# only one actually implemented; everything else returns from UnregisteredAPIAdapter
# and the router falls back to Playwright.
API_REGISTRY: dict[str, type] = {
    "bluesky":   BlueskyAdapter,
    "x":         XAPIAdapter,
    "linkedin":  LinkedinAPIAdapter,
    "pinterest": PinterestAPIAdapter,
    "youtube":   YoutubeAPIAdapter,
    "facebook":  MetaAdapter,
    "instagram": MetaAdapter,
    "threads":   MetaAdapter,
    "tiktok":    TiktokAPIAdapter,
}

__all__ = [
    "APINotRegisteredError", "UnregisteredAPIAdapter",
    "BlueskyAdapter", "LinkedinAPIAdapter", "MetaAdapter",
    "PinterestAPIAdapter", "TiktokAPIAdapter", "XAPIAdapter",
    "YoutubeAPIAdapter", "API_REGISTRY",
]
