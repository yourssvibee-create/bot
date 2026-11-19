"""Reddit scraper via praw. Skipped (and logged once) while karma-gated."""
from __future__ import annotations

import hashlib
from typing import Any

from shared.logging import get_logger

from ..config import get, reddit_enabled

log = get_logger("bot1.scraper.reddit")

SUBS_DEFAULT = ["smallbusiness", "Entrepreneur", "marketing", "reviews"]
QUERIES_DEFAULT = [
    "negative reviews",
    "bad reviews help",
    "reputation management",
    "improve online reviews",
]

_SKIP_LOGGED = False


def _sid(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:24]


def scrape(subs: list[str] | None = None, queries: list[str] | None = None) -> list[dict[str, Any]]:
    global _SKIP_LOGGED
    if not reddit_enabled():
        if not _SKIP_LOGGED:
            log.info("reddit_skipped_karma_gate")
            _SKIP_LOGGED = True
        return []
    # Lazy import so we don't require praw to be installed at import time.
    import praw  # noqa: WPS433

    reddit = praw.Reddit(
        client_id=get("REDDIT_CLIENT_ID"),
        client_secret=get("REDDIT_CLIENT_SECRET"),
        username=get("REDDIT_USERNAME"),
        password=get("REDDIT_PASSWORD"),
        user_agent=get("REDDIT_USER_AGENT", "ominvo-bot1/0.1"),
    )

    subs = subs or SUBS_DEFAULT
    qs = queries or QUERIES_DEFAULT
    out: list[dict[str, Any]] = []
    for sub in subs:
        for q in qs:
            try:
                for post in reddit.subreddit(sub).search(q, sort="new", time_filter="week", limit=10):
                    url = f"https://www.reddit.com{post.permalink}"
                    out.append({
                        "source": "reddit",
                        "source_id": _sid(url),
                        "url": url,
                        "author": str(post.author) if post.author else None,
                        "title": post.title[:300],
                        "body": (post.selftext or "")[:2000],
                        "raw": {"sub": sub, "query": q, "score": post.score},
                    })
            except Exception as e:
                log.warning("reddit_search_fail", extra={"sub": sub, "q": q, "err": str(e)})
    log.info("reddit_done", extra={"count": len(out)})
    return out
