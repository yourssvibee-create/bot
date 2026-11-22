"""Cross-bot daily promo caps.

The plan reserves three platforms where Bot 1 (intent-hijacker) and Bot 2
(social-factory) share an underlying account: `reddit`, `quora`, `x`. Posting
too hard from either side will trip anti-spam heuristics on the shared
account, so this module merges today's counts across both bots' dispatch
tables and tells a caller whether another post for that platform is safe.

Counters we read:
  - bot2_dispatches  (status in ('posted','skipped_dry_run')) — Bot 2 posts
  - replies_sent     (outcome='ok')                           — Bot 1 replies

Defaults (combined across bots, per calendar day UTC):
  reddit: 4   quora: 3   x: 5

Override with SHARED_CAP_<PLATFORM>=<int> env (e.g. SHARED_CAP_X=8). Set
SHARED_CAPS_RELAX=1 to bypass entirely (debug/CI dry-run).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from .logging import get_logger

log = get_logger("shared.caps")

SHARED_PLATFORMS: frozenset[str] = frozenset({"reddit", "quora", "x"})

_DEFAULTS: dict[str, int] = {
    "reddit": 4,
    "quora":  3,
    "x":      5,
}


def _is_relaxed() -> bool:
    return (os.environ.get("SHARED_CAPS_RELAX") or "").strip() == "1"


def _cap_for(platform: str) -> int:
    env = os.environ.get(f"SHARED_CAP_{platform.upper()}")
    if env and env.strip().isdigit():
        return int(env.strip())
    return _DEFAULTS.get(platform, 0)


def today_count(platform: str) -> int:
    """Total posts today (UTC) for this platform across both bots.

    Returns 0 if the DB is unreachable — callers treat "unknown" as "allowed"
    so a transient DB blip can't silently block all posting. If that's not
    acceptable, call `today_count_strict` which re-raises.
    """
    try:
        return today_count_strict(platform)
    except Exception as e:
        log.warning("shared_caps_db_err",
                    extra={"platform": platform, "err": str(e)[:200]})
        return 0


def today_count_strict(platform: str) -> int:
    from .db import fetch_one
    start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    total = 0
    # Bot 2's dispatches table
    try:
        row = fetch_one(
            "select count(*)::int as n from bot2_dispatches "
            "where platform = %s and posted_at >= %s "
            "and status in ('posted','skipped_dry_run')",
            (platform, start),
        )
        total += int((row or {}).get("n") or 0)
    except Exception as e:
        log.warning("shared_caps_bot2_err",
                    extra={"platform": platform, "err": str(e)[:200]})
    # Bot 1's replies_sent table (if present). The schema is different;
    # we only count rows with outcome='ok' whose drafted channel matches.
    try:
        row = fetch_one(
            "select count(*)::int as n from replies_sent "
            "where channel = %s and outcome = 'ok' "
            "and created_at >= %s",
            (platform, start),
        )
        total += int((row or {}).get("n") or 0)
    except Exception as e:
        # replies_sent may not exist in every DB schema version — swallow
        # that particular case, propagate others.
        msg = str(e).lower()
        if "does not exist" not in msg and "relation" not in msg:
            log.warning("shared_caps_bot1_err",
                        extra={"platform": platform, "err": str(e)[:200]})
    return total


def allowed(platform: str) -> tuple[bool, str]:
    """(ok, reason) — is another post to `platform` allowed under the shared cap?"""
    if _is_relaxed():
        return True, "shared_caps_relaxed"
    if platform not in SHARED_PLATFORMS:
        return True, "not_shared"
    cap = _cap_for(platform)
    if cap <= 0:
        return True, "no_cap_configured"
    n = today_count(platform)
    if n >= cap:
        return False, f"shared_cap_reached_{n}/{cap}"
    return True, f"ok_{n}/{cap}"
