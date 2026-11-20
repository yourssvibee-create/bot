"""Bot 2 Playwright adapters — one class per platform.

post() raises NotImplementedError for every adapter; filled per-platform in a
later pass that prefers official APIs where available (X, LinkedIn, Pinterest,
YouTube, Meta/FB+IG, TikTok) and keeps Playwright for Quora, Medium, Nextdoor,
Threads.
"""
from .facebook import FacebookAdapter
from .instagram import InstagramAdapter
from .linkedin import LinkedInAdapter
from .medium import MediumAdapter
from .nextdoor import NextdoorAdapter
from .pinterest import PinterestAdapter
from .quora import QuoraAdapter
from .threads import ThreadsAdapter
from .tiktok import TikTokAdapter
from .x import XAdapter
from .youtube import YouTubeAdapter

__all__ = [
    "FacebookAdapter", "InstagramAdapter", "LinkedInAdapter", "MediumAdapter",
    "NextdoorAdapter", "PinterestAdapter", "QuoraAdapter", "ThreadsAdapter",
    "TikTokAdapter", "XAdapter", "YouTubeAdapter",
]
