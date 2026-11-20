"""MetaAdapter stub — covers Facebook, Instagram, Threads via Meta Graph.

Raises APINotRegisteredError until tokens are populated in .env.
Router catches this and falls back to the Playwright sibling.
"""
from __future__ import annotations

from ._api_stub import UnregisteredAPIAdapter


class MetaAdapter(UnregisteredAPIAdapter):
    platform = "meta"
