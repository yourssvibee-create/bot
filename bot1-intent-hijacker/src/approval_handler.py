"""Bot 1 handler for the shared Telegram approval poll.

Callback contract (set by src/telegram_approve.py enqueue()):
  approve:<drafted_id>
  reject:<drafted_id>

Action: flip the `replies_drafted` row to 'approved' / 'rejected' and
ack the user with a toast. The nightly loop polls the DB for the
decision and dispatches the reply on 'approved'.

This replaces the Vercel webhook at api/telegram_webhook.py — same
semantics, different delivery. The webhook file stays in the repo
for now as reference; the shared poll + the deleted Telegram
webhook make it dormant.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.approval_poll_core import register_callback
from shared.db import execute
from shared.logging import get_logger
from shared.telegram import answer_callback_query, edit_message_text

log = get_logger("bot1.approval_handler")


def _handle(action: str, drafted_id: str, cq: dict[str, Any]) -> None:
    cq_id = cq.get("id") or ""
    new_status = "approved" if action == "approve" else "rejected"
    try:
        execute(
            "update replies_drafted set status=%s, decided_at=now() "
            "where id=%s and status='pending'",
            (new_status, drafted_id),
        )
    except Exception as e:
        log.warning("bot1.update.fail id=%s err=%s", drafted_id, e)
        if cq_id:
            answer_callback_query(cq_id, f"db error: {e}")
        return
    log.info("bot1.decided id=%s status=%s", drafted_id, new_status)
    if cq_id:
        answer_callback_query(cq_id, f"marked {new_status}")

    msg = cq.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    message_id = msg.get("message_id")
    if chat_id and message_id:
        # Replace the buttons with a status footer so the operator
        # sees which drafts are decided at a glance.
        original_text = msg.get("text") or ""
        try:
            edit_message_text(
                chat_id=chat_id, message_id=int(message_id),
                text=f"{original_text}\n\n<b>{new_status}</b>",
                keyboard=[],
            )
        except Exception as e:
            log.warning("bot1.edit.fail id=%s err=%s", drafted_id, e)


def _on_approve(cq: dict[str, Any]) -> None:
    data = cq.get("data") or ""
    _, _, drafted_id = data.partition(":")
    if drafted_id:
        _handle("approve", drafted_id, cq)


def _on_reject(cq: dict[str, Any]) -> None:
    data = cq.get("data") or ""
    _, _, drafted_id = data.partition(":")
    if drafted_id:
        _handle("reject", drafted_id, cq)


register_callback("approve:", _on_approve)
register_callback("reject:",  _on_reject)
