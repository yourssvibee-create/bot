"""Per-platform safety caps and failure-pattern detection for Bot 2.

First-7-day caps (overrideable with env SAFETY_CAPS_RELAX=1):

    instagram:  1 post/day, 12:00-21:00 local window
    tiktok:     1 post/day, 12:00-21:00 local window
    facebook:   2 posts/day
    youtube:    2 posts/day (Shorts)
    threads:    3 posts/day
    [others]:   3 posts/day

On post failure whose error text matches one of RED_FLAG_PATTERNS, the router
flips bot2_account_health.status='red' for that platform, Telegram-alerts
chat 5301190192, and pauses the platform for 48h.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, time, timezone

from shared.logging import get_logger

log = get_logger("bot2.safety")


@dataclass(frozen=True)
class SafetyCap:
    platform: str
    max_per_day: int
    window_start: time | None = None   # local time; None = no window
    window_end: time | None = None


_CAPS: dict[str, SafetyCap] = {
    "instagram": SafetyCap("instagram", 1, time(12, 0), time(21, 0)),
    "tiktok":    SafetyCap("tiktok",    1, time(12, 0), time(21, 0)),
    "facebook":  SafetyCap("facebook",  2),
    "youtube":   SafetyCap("youtube",   2),
    "threads":   SafetyCap("threads",   3),
    "bluesky":   SafetyCap("bluesky",   3),
    "x":         SafetyCap("x",         3),
    "linkedin":  SafetyCap("linkedin",  3),
    "medium":    SafetyCap("medium",    3),
    "quora":     SafetyCap("quora",     3),
    "nextdoor":  SafetyCap("nextdoor",  3),
    "pinterest": SafetyCap("pinterest", 3),
}

RED_FLAG_PATTERNS = re.compile(
    r"unusual[_ ]activity|verification[_ ]required|account[_ ]lock"
    r"|captcha|rate[_ ]limit|rate[_ ]limited|too many requests",
    re.IGNORECASE,
)

_PAUSE_HOURS = 48


def get_cap(platform: str) -> SafetyCap:
    return _CAPS.get(platform, SafetyCap(platform, 3))


def within_window(cap: SafetyCap, now_local: datetime) -> bool:
    if cap.window_start is None or cap.window_end is None:
        return True
    t = now_local.time()
    return cap.window_start <= t <= cap.window_end


def caps_relaxed() -> bool:
    return (os.environ.get("SAFETY_CAPS_RELAX") or "").strip() == "1"


def is_red_flag(error_text: str | None) -> bool:
    return bool(error_text and RED_FLAG_PATTERNS.search(error_text))


def allowed(platform: str, posts_so_far_today: int, now_local: datetime) -> tuple[bool, str]:
    """Return (ok, reason) for a prospective post."""
    if caps_relaxed():
        return True, "caps_relaxed"
    cap = get_cap(platform)
    if posts_so_far_today >= cap.max_per_day:
        return False, f"cap_reached_{cap.max_per_day}"
    if not within_window(cap, now_local):
        return False, "outside_window"
    return True, "ok"


def on_red_flag(platform: str, error_text: str) -> None:
    """Flip account health to red, telegram-alert, mark paused for 48h.

    Imports are lazy so this module stays import-safe without DB credentials.
    """
    msg = (
        f"<b>bot2 RED FLAG</b> platform=<code>{platform}</code>\n"
        f"pattern matched → status=red, paused {_PAUSE_HOURS}h\n"
        f"err: {error_text[:300]}"
    )
    try:
        from shared.db import execute
        execute(
            "insert into bot2_account_health (platform, status, notes) "
            "values (%s, 'red', %s) "
            "on conflict (platform) do update set status='red', notes=excluded.notes",
            (platform, f"paused_until:{_pause_until_iso()} err:{error_text[:200]}"),
        )
    except Exception as e:
        log.warning("red_flag_db_fail", extra={"err": str(e)[:200], "platform": platform})
    try:
        from shared.telegram import send_message
        send_message(msg)
    except Exception as e:
        log.warning("red_flag_tg_fail", extra={"err": str(e)[:200], "platform": platform})
    log.warning("red_flag", extra={"platform": platform, "err": error_text[:200]})


def _pause_until_iso() -> str:
    from datetime import timedelta
    return (datetime.now(timezone.utc) + timedelta(hours=_PAUSE_HOURS)).isoformat()
