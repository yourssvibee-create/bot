"""Bot 2 Telegram handlers for slash commands.

Commands (namespaced /bot2_*):
  /bot2_approve_today — mark every row in bot2_content_queue status='queued'
                        as 'approved' (adapter dispatch proceeds).
  /bot2_pause         — set bot2_account_health notes='paused' across all
                        platforms; router skips platforms flagged paused.
  /bot2_stats         — reply with today's queue + dispatch counts.

These handlers return dicts that `api/telegram_webhook.py` serialises back to
the Telegram API as sendMessage payloads. No long-running work in-handler —
the webhook is time-bound.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from shared.logging import get_logger

log = get_logger("bot2.telegram")


CommandResult = dict[str, Any]


def _reply(text: str) -> CommandResult:
    return {"ok": True, "reply": text}


def _today_bounds() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    end = now.replace(hour=23, minute=59, second=59, microsecond=0).isoformat()
    return start, end


def handle_approve_today() -> CommandResult:
    """Approve every queued row created today."""
    start, _ = _today_bounds()
    try:
        from shared.db import execute
    except Exception as e:
        return _reply(f"approve_today: db unavailable ({e})")
    rows = execute(
        "update bot2_content_queue set status='approved' "
        "where status='queued' and created_at >= %s "
        "returning id",
        (start,),
    )
    n = len(rows) if isinstance(rows, list) else 0
    log.info("bot2_approve_today", extra={"approved": n})
    return _reply(f"approved {n} queued item(s) from today")


def handle_pause() -> CommandResult:
    """Pause all platforms."""
    try:
        from shared.db import execute
    except Exception as e:
        return _reply(f"pause: db unavailable ({e})")
    execute(
        "update bot2_account_health set notes='paused', status='yellow' "
        "where coalesce(notes, '') <> 'paused'",
    )
    log.info("bot2_pause")
    return _reply("bot2: all platforms paused (notes='paused')")


def handle_stats() -> CommandResult:
    """Return today's counts."""
    start, _ = _today_bounds()
    try:
        from shared.db import fetch_one
    except Exception as e:
        return _reply(f"stats: db unavailable ({e})")
    q = fetch_one(
        "select count(*) as n_queue from bot2_content_queue where created_at >= %s",
        (start,),
    ) or {}
    d = fetch_one(
        "select count(*) as n_disp, "
        "count(*) filter (where status='posted') as n_posted, "
        "count(*) filter (where status='failed') as n_failed "
        "from bot2_dispatches where posted_at >= %s",
        (start,),
    ) or {}
    return _reply(
        f"bot2 today: queued={q.get('n_queue', 0)} "
        f"dispatched={d.get('n_disp', 0)} "
        f"posted={d.get('n_posted', 0)} "
        f"failed={d.get('n_failed', 0)}"
    )


# Registry used by the webhook. Each key is the exact slash command text
# (without the leading /). Extend here when adding new /bot2_ commands.
COMMANDS: dict[str, Any] = {
    "bot2_approve_today": handle_approve_today,
    "bot2_pause":         handle_pause,
    "bot2_stats":         handle_stats,
}


def dispatch_command(text: str) -> CommandResult:
    """Parse a Telegram message body and route to a handler."""
    cmd = (text or "").strip().lstrip("/").split("@", 1)[0].split()[0] if text else ""
    handler = COMMANDS.get(cmd)
    if handler is None:
        return _reply(f"unknown command: /{cmd}. try /bot2_stats")
    return handler()
