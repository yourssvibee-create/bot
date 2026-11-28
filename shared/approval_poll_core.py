"""Shared Telegram poll loop with pluggable per-bot handlers.

One Telegram bot now serves every bot in this repo (bot1, bot2, bot7,
…). A single GH Actions cron workflow runs this poller every 5 min:

  1. Read `telegram_offset.last_offset` (shared cursor table).
  2. Call Telegram getUpdates with offset+1.
  3. For each update, dispatch:
     - callback_query → the registered handler whose prefix matches
       `callback_query.data` (longest-prefix wins — so "approve:" and
       "approve_today:" can coexist).
     - message         → each registered message handler, in insertion
       order, until one returns True (claimed).
  4. Persist new offset = max(update_id).

Per-bot handlers register at import time by calling
`register_callback(prefix, handler)` / `register_message(handler)`.
The entrypoint script imports every bot's `approval_handler` module
so the registrations happen before `poll_once` runs.

Handlers are thin: they parse their own callback/message payload and
do whatever Postgres + API work they need. Any raised exception is
caught, logged, and does NOT block other handlers from running on
later updates. The offset is advanced only for updates we fully
walked (any handler exception still counts as walked — Telegram
re-delivers forever otherwise and one bad row stalls everyone).
"""
from __future__ import annotations

from typing import Any, Callable

from .logging import get_logger
from .telegram import get_updates

log = get_logger("shared.approval_poll")

# prefix (e.g. "approve:", "a:", "et:") -> callable(callback_query_dict)
_CALLBACK_HANDLERS: list[tuple[str, Callable[[dict[str, Any]], None]]] = []

# Message handlers are tried in registration order. The first to
# return a truthy value "claims" the message; later handlers do not
# run for it.
_MESSAGE_HANDLERS: list[Callable[[dict[str, Any]], bool]] = []


def register_callback(prefix: str, handler: Callable[[dict[str, Any]], None]) -> None:
    """Register a handler for callback_query.data starting with `prefix`.
    The prefix is matched literally (no regex, no colons implied).
    Longer prefixes take precedence over shorter ones at dispatch time."""
    if not prefix:
        raise ValueError("callback prefix must be non-empty")
    _CALLBACK_HANDLERS.append((prefix, handler))
    _CALLBACK_HANDLERS.sort(key=lambda t: len(t[0]), reverse=True)


def register_message(handler: Callable[[dict[str, Any]], bool]) -> None:
    """Register a handler for plain text / reply messages. Returning
    truthy claims the message and stops further handlers from seeing
    it. Returning falsy means 'not mine, try the next one'."""
    _MESSAGE_HANDLERS.append(handler)


def _dispatch_callback(cq: dict[str, Any]) -> None:
    data = cq.get("data") or ""
    for prefix, handler in _CALLBACK_HANDLERS:
        if data.startswith(prefix):
            try:
                handler(cq)
            except Exception as e:
                log.warning("callback.handler.exc prefix=%s err=%s", prefix, e)
            return
    log.info("callback.unhandled data=%s", data[:80])


def _dispatch_message(msg: dict[str, Any]) -> None:
    for handler in _MESSAGE_HANDLERS:
        try:
            if handler(msg):
                return
        except Exception as e:
            log.warning("message.handler.exc err=%s", e)
    # not claimed — fine, not every text is for us.


def poll_once(
    *,
    get_offset: Callable[[], int],
    set_offset: Callable[[int], None],
    timeout_s: int = 5,
) -> int:
    """Pull one batch of updates and dispatch them. Returns the number
    of updates processed. Safe to call in a tight loop (it long-polls
    at Telegram's end)."""
    offset = get_offset()
    updates = get_updates(offset + 1 if offset else 0, timeout=timeout_s)
    if not updates:
        log.info("poll.empty offset=%s", offset)
        return 0

    last_update_id = 0
    for upd in updates:
        last_update_id = max(last_update_id, int(upd.get("update_id") or 0))
        if "callback_query" in upd:
            _dispatch_callback(upd["callback_query"])
        elif "message" in upd:
            _dispatch_message(upd["message"])
        elif "edited_message" in upd:
            _dispatch_message(upd["edited_message"])

    if last_update_id:
        set_offset(last_update_id)
    log.info("poll.done processed=%s new_offset=%s", len(updates), last_update_id)
    return len(updates)
