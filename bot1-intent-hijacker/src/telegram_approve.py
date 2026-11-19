"""Queue drafted replies for Telegram approval, poll DB for the decision.

Flow:
  1. nightly_run inserts a row in `replies_drafted` with status='pending'.
  2. `queue(...)` sends the draft to Telegram with Approve/Reject buttons
     whose callback_data carries the drafted-reply id.
  3. The Vercel webhook (`api/telegram_webhook.py`) receives the callback and
     flips the row to 'approved' / 'rejected'.
  4. `wait_for_decision` polls the DB until a decision lands or the timeout
     is hit; on timeout it marks the row 'hold'.
"""
from __future__ import annotations

import html
import time
from typing import Literal

from shared.db import execute, fetch_one, upsert
from shared.logging import get_logger
from shared.telegram import send_approval

from .config import settings

log = get_logger("bot1.approve")

Decision = Literal["approved", "rejected", "hold", "pending"]


def enqueue(lead_id: str, channel: str, draft_text: str) -> str:
    row = upsert(
        "replies_drafted",
        {
            "lead_id": lead_id,
            "channel": channel,
            "draft_text": draft_text,
            "status": "pending",
        },
        conflict_cols=["id"],  # id is auto-generated, upsert never conflicts
    )
    drafted_id = str(row["id"]) if row else None
    if not drafted_id:
        # Fallback — insert plainly
        r = fetch_one(
            "insert into replies_drafted (lead_id, channel, draft_text, status) "
            "values (%s, %s, %s, 'pending') returning id",
            (lead_id, channel, draft_text),
        )
        drafted_id = str(r["id"])

    header = f"<b>Draft reply</b> · channel=<code>{html.escape(channel)}</code>\nlead=<code>{html.escape(str(lead_id))}</code>"
    body_safe = html.escape(draft_text[:3500])
    msg = f"{header}\n\n<pre>{body_safe}</pre>"
    try:
        resp = send_approval(msg, callback_data=drafted_id)
        tg_msg_id = str(resp.get("result", {}).get("message_id", "") or "")
        if tg_msg_id:
            execute(
                "update replies_drafted set telegram_msg_id=%s where id=%s",
                (tg_msg_id, drafted_id),
            )
    except Exception as e:
        log.warning("telegram_send_fail", extra={"err": str(e), "drafted_id": drafted_id})
    return drafted_id


def wait_for_decision(drafted_id: str, *,
                      poll_sec: float = 5.0,
                      timeout_hours: int | None = None) -> Decision:
    """Poll `replies_drafted` until a decision or timeout."""
    cfg = settings()
    deadline = time.time() + (timeout_hours or cfg.telegram_approval_timeout_hours) * 3600
    while time.time() < deadline:
        row = fetch_one("select status from replies_drafted where id=%s", (drafted_id,))
        if row and row["status"] in ("approved", "rejected"):
            return row["status"]  # type: ignore[return-value]
        time.sleep(poll_sec)
    execute(
        "update replies_drafted set status='hold', decided_at=now() "
        "where id=%s and status='pending'",
        (drafted_id,),
    )
    log.info("approval_timeout_hold", extra={"drafted_id": drafted_id})
    return "hold"
