"""Universal morning approval digest.

Collects pending items from every registered bot provider, assembles ONE
Telegram card with per-item Approve / Reject / Edit buttons plus a
single [✅ Approve All] button at the top, and sends it.

Per-item buttons reuse each bot's existing callback_data prefixes
(a:<seg>, r:<seg>, et:<seg> for bot7; bot1/bot2 add their own when they
register providers), so the shared approval poll worker routes taps to
the right handler without new wiring.

The "Approve All" callback uses the reserved prefix `aa:all` and is
handled by shared.approval_digest_aa.register_aa_handler(), which
collects pending items again at tap time and calls each registered
bot's approve path. Each bot registers an `aa_approve` function at
import time; providers without one are skipped.
"""
from __future__ import annotations

import html
import os
from typing import Callable

from .digest_registry import PendingItem, collect_pending
from .logging import get_logger
from .telegram import edit_message_text, send_with_keyboard

log = get_logger("shared.approval_digest")

# Bot-id -> callable(item_id) that approves that bot's item. Registered
# at import time by each bot's approval_handler.
_AA_APPROVERS: dict[str, Callable[[str], bool]] = {}


def register_aa_approver(bot_id: str, fn: Callable[[str], bool]) -> None:
    """Called by each bot's approval_handler at import time. `fn(item_id)`
    publishes the item and returns True on success."""
    _AA_APPROVERS[bot_id] = fn


def _keyboard(items: list[PendingItem]) -> list[list[dict[str, str]]]:
    rows: list[list[dict[str, str]]] = []
    # Header: Approve All (single button, full width)
    rows.append([{"text": "✅ Approve All", "callback_data": "aa:all"}])
    # Per-item: compact 3-button row per pending pack
    for idx, it in enumerate(items, 1):
        rows.append([
            {"text": f"✅ {idx}",  "callback_data": it.get("approve_cb", "noop:")},
            {"text": f"✏️ {idx}", "callback_data": it.get("edit_cb",    "noop:")},
            {"text": f"❌ {idx}",  "callback_data": it.get("reject_cb",  "noop:")},
        ])
    return rows


def _format_body(items: list[PendingItem]) -> str:
    esc = html.escape
    if not items:
        return "🌅 <b>Morning digest</b>\n\nNothing pending. ☕"
    lines = [f"🌅 <b>Morning digest</b> — {len(items)} pending\n"]
    for idx, it in enumerate(items, 1):
        bot = it.get("bot", "?")
        title = esc(it.get("title", "(no title)")[:120])
        subtitle = esc(it.get("subtitle", "")[:120])
        lines.append(f"<b>{idx}.</b> [{bot}] {title}")
        if subtitle:
            lines.append(f"    <i>{subtitle}</i>")
    lines.append("\nTap <b>Approve All</b>, or use per-item buttons below.")
    return "\n".join(lines)


def send_morning_digest() -> int | None:
    """Collect pending items and send ONE Telegram card. Returns the
    sent message_id (or None on failure / nothing to send). Called by
    workers/approval_digest.py on a cron schedule."""
    items = collect_pending()
    if not items:
        log.info("digest.empty")
        # Still send an 'all clear' card so operator knows the job ran.
        text = _format_body([])
        try:
            send_with_keyboard(text, keyboard=[])
        except Exception as e:
            log.warning("digest.empty_send.fail err=%s", e)
        return None
    text = _format_body(items)
    try:
        body = send_with_keyboard(text, keyboard=_keyboard(items))
    except Exception as e:
        log.warning("digest.send.fail err=%s", e)
        return None
    msg_id = ((body.get("result") or {}).get("message_id"))
    log.info("digest.send.ok n=%s msg_id=%s", len(items), msg_id)
    return int(msg_id) if msg_id else None


def handle_approve_all(cq: dict) -> None:
    """Called by the shared poll worker when the user taps [✅ Approve All].
    Collects pending items fresh (not from the message), then calls the
    per-bot approver for each. Edits the card to a running status."""
    msg = cq.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id") or ""
    message_id = msg.get("message_id")

    items = collect_pending()
    if not items:
        if message_id:
            try:
                edit_message_text(
                    chat_id=chat_id, message_id=int(message_id),
                    text="🌅 <b>Morning digest</b>\n\nNothing left pending. ☕",
                    keyboard=[],
                )
            except Exception:
                pass
        return

    ok = 0
    fail = 0
    for it in items:
        bot = it.get("bot", "")
        item_id = it.get("item_id", "")
        approver = _AA_APPROVERS.get(bot)
        if not approver or not item_id:
            fail += 1
            continue
        try:
            if approver(item_id):
                ok += 1
            else:
                fail += 1
        except Exception as e:
            log.warning("aa.approve.exc bot=%s id=%s err=%s", bot, item_id, e)
            fail += 1

    summary = (
        f"🌅 <b>Morning digest</b> — Approve All done\n"
        f"✅ Published: {ok}   ⚠️ Failed: {fail}"
    )
    if message_id:
        try:
            edit_message_text(
                chat_id=chat_id, message_id=int(message_id),
                text=summary, keyboard=[],
            )
        except Exception as e:
            log.warning("aa.edit.fail err=%s", e)
    log.info("aa.done ok=%s fail=%s", ok, fail)
