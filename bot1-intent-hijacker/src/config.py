"""Bot 1 configuration — quotas, timeouts, feature flags."""
from __future__ import annotations

from dataclasses import dataclass

# Make `shared/` importable when run from inside bot1-intent-hijacker/.
import sys
from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.config import get, get_bool, get_int, is_placeholder  # noqa: E402


@dataclass(frozen=True)
class Bot1Settings:
    dry_run: bool
    max_signals_per_source: int
    min_intent_score: int
    polite_delay_trustpilot: float = 4.0
    polite_delay_quora: float = 3.0
    polite_delay_nitter: float = 2.0
    polite_delay_reddit: float = 2.0
    telegram_approval_timeout_hours: int = 12
    contact_form_timeout_sec: int = 30
    social_dispatch_timeout_sec: int = 30
    playwright_headless: bool = True


def settings() -> Bot1Settings:
    return Bot1Settings(
        dry_run=get_bool("BOT1_DRY_RUN", False),
        max_signals_per_source=get_int("BOT1_MAX_SIGNALS_PER_SOURCE", 40),
        min_intent_score=get_int("BOT1_MIN_INTENT_SCORE", 65),
        telegram_approval_timeout_hours=get_int("BOT1_APPROVAL_TIMEOUT_H", 12),
        playwright_headless=get_bool("BOT1_HEADLESS", True),
    )


def reddit_enabled() -> bool:
    cid = get("REDDIT_CLIENT_ID")
    sec = get("REDDIT_CLIENT_SECRET")
    return not (is_placeholder(cid) or is_placeholder(sec))


_NITTER_FALLBACK = [
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.net",
    "https://nitter.1d4.us",
    "https://nitter.kavin.rocks",
    "https://nitter.unixfox.eu",
    "https://nitter.fdn.fr",
    "https://nitter.cz",
    "https://xcancel.com",
]


def nitter_instances() -> list[str]:
    raw = get("NITTER_INSTANCES", "") or ""
    instances = [u.strip() for u in raw.split(",") if u.strip()]
    return instances or list(_NITTER_FALLBACK)


__all__ = [
    "Bot1Settings",
    "settings",
    "reddit_enabled",
    "nitter_instances",
    "is_placeholder",
    "get",
]
