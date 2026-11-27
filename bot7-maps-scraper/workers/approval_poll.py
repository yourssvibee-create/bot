"""Approval poller — runs every 5 min via GH Actions cron.

Pulls the next page of Telegram updates with getUpdates(offset), then
walks each update. There are two input kinds we care about:

  1) callback_query: operator tapped a button on an approval prompt.
     - a:<seg>   → publish to gumroad + whop, edit the prompt to '✅ Live'.
     - r:<seg>   → mark rejected, edit the prompt to '❌ Rejected'.
     - et|ep|ed:<seg> → send a force_reply prompt asking for the new
       value; remember (chat_id, prompt_message_id, field) on the pack
       row so the next update can match the reply back.

  2) message (with reply_to_message): operator replied to one of the
     force_reply prompts. Look up the pending edit by
     reply_to_message.message_id, apply the new value, push the
     updated field back to the channel drafts, re-send a fresh
     approval prompt with the updated preview.

All DB access goes through bot7_packs. All channel API access goes
through src/listing/approve.py and the adapters' update() paths. The
poller is idempotent on Telegram's side — Telegram keeps delivering
the same update until we advance `offset`, so if we crash
mid-processing the next run picks it up again.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

import httpx

_BOT_ROOT = Path(__file__).resolve().parents[1]           # bot7-maps-scraper/
_REPO_ROOT = Path(__file__).resolve().parents[2]          # repo root (for `shared`)
for p in (_BOT_ROOT, _REPO_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from shared.logging import get_logger
from shared.telegram import (
    answer_callback_query,
    get_updates,
    send_force_reply,
)

from src.notify.approval import (  # noqa: E402
    mark_approved, mark_failed, mark_rejected, send_approval_prompt,
)
from src.listing.approve import publish_pack  # noqa: E402
from src.storage.supabase_client import (  # noqa: E402
    apply_pack_edit,
    clear_pack_edit_pending,
    find_pack_by_message,
    find_pack_by_segment,
    get_telegram_offset,
    mark_pack_published,
    mark_pack_rejected,
    set_pack_approval_message,
    set_pack_edit_pending,
    set_telegram_offset,
)

log = get_logger("bot7.approval_poll")


# callback_data format: "<op>:<segment_slug>"
_CB_RE = re.compile(r"^(a|r|et|ep|ed):(.+)$")
_FIELD_BY_OP = {"et": "title", "ep": "price", "ed": "description"}
_FIELD_LABEL = {"title": "title", "price": "price (USD)", "description": "description"}


def _handle_approve(cq: dict[str, Any], segment_slug: str) -> None:
    pack = find_pack_by_segment(segment_slug)
    if not pack:
        log.warning("approve.no_pack seg=%s", segment_slug)
        return
    msg = cq.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id") or ""
    message_id = msg.get("message_id")

    res = publish_pack(
        gumroad_pid=pack.get("gumroad_product_id") or "",
        whop_pid=pack.get("whop_product_id") or "",
    )
    operator = (cq.get("from") or {}).get("username") or (cq.get("from") or {}).get("id") or "?"

    if res.gumroad_ok and res.whop_ok:
        mark_pack_published(
            segment_slug=segment_slug, approved_by=str(operator),
            gumroad_live_url=res.gumroad_url, whop_live_url=res.whop_url,
        )
        if message_id:
            mark_approved(
                chat_id=chat_id, message_id=int(message_id),
                segment_slug=segment_slug,
                gumroad_live_url=res.gumroad_url,
                whop_live_url=res.whop_url,
            )
    else:
        mark_pack_published(
            segment_slug=segment_slug, approved_by=str(operator),
            publish_error=res.error or "partial publish",
        )
        if message_id:
            mark_failed(
                chat_id=chat_id, message_id=int(message_id),
                segment_slug=segment_slug, err=res.error or "partial publish",
            )


def _handle_reject(cq: dict[str, Any], segment_slug: str) -> None:
    operator = (cq.get("from") or {}).get("username") or (cq.get("from") or {}).get("id") or "?"
    mark_pack_rejected(segment_slug=segment_slug, approved_by=str(operator))
    msg = cq.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id") or ""
    message_id = msg.get("message_id")
    if message_id:
        mark_rejected(chat_id=chat_id, message_id=int(message_id), segment_slug=segment_slug)


def _handle_edit_prompt(cq: dict[str, Any], segment_slug: str, field: str) -> None:
    pack = find_pack_by_segment(segment_slug)
    if not pack:
        log.warning("edit.no_pack seg=%s field=%s", segment_slug, field)
        return
    current = ""
    if field == "title":
        current = pack.get("title") or ""
    elif field == "description":
        current = pack.get("description") or ""
    elif field == "price":
        cents = pack.get("price_cents") or 0
        current = str(int(cents) // 100) if cents else ""

    prompt_text = (
        f"✏️ Reply with the new <b>{_FIELD_LABEL[field]}</b> for "
        f"<code>{segment_slug}</code>.\n"
        f"Current: <code>{current[:200]}</code>"
    )
    try:
        body = send_force_reply(prompt_text)
    except Exception as e:
        log.warning("edit.prompt.fail seg=%s field=%s err=%s", segment_slug, field, e)
        return
    sent = body.get("result") or {}
    prompt_msg_id = int(sent.get("message_id") or 0)
    prompt_chat_id = str((sent.get("chat") or {}).get("id") or "")
    if prompt_msg_id and prompt_chat_id:
        set_pack_edit_pending(
            segment_slug=segment_slug, field=field,
            prompt_chat_id=prompt_chat_id, prompt_message_id=prompt_msg_id,
        )


def _resend_approval(segment_slug: str) -> None:
    pack = find_pack_by_segment(segment_slug)
    if not pack:
        return
    msg_id = send_approval_prompt(
        segment_slug=segment_slug,
        title=pack.get("title") or segment_slug,
        price_usd=int((pack.get("price_cents") or 0) // 100),
        price_kind=pack.get("price_kind") or "one_time",
        record_count=int(pack.get("record_count") or 0),
        emails_found=0,
        gumroad_url="",
        whop_url="",
        download_url=pack.get("download_url") or "",
    )
    if msg_id:
        set_pack_approval_message(
            segment_slug=segment_slug,
            chat_id=pack.get("edit_prompt_chat_id") or "",
            message_id=msg_id,
        )


def _handle_callback(cq: dict[str, Any]) -> None:
    data = cq.get("data") or ""
    m = _CB_RE.match(data)
    cq_id = cq.get("id") or ""
    if not m:
        log.warning("callback.unknown data=%s", data[:80])
        if cq_id:
            answer_callback_query(cq_id, "unknown action")
        return
    op, segment_slug = m.group(1), m.group(2)
    log.info("callback op=%s seg=%s", op, segment_slug)

    if op == "a":
        if cq_id:
            answer_callback_query(cq_id, "Publishing…")
        _handle_approve(cq, segment_slug)
    elif op == "r":
        if cq_id:
            answer_callback_query(cq_id, "Rejected")
        _handle_reject(cq, segment_slug)
    elif op in _FIELD_BY_OP:
        if cq_id:
            answer_callback_query(cq_id, "Reply with the new value")
        _handle_edit_prompt(cq, segment_slug, _FIELD_BY_OP[op])
    else:
        if cq_id:
            answer_callback_query(cq_id, "unknown")


def _handle_reply(msg: dict[str, Any]) -> None:
    reply_to = msg.get("reply_to_message") or {}
    reply_to_id = reply_to.get("message_id")
    chat_id = (msg.get("chat") or {}).get("id") or ""
    if not reply_to_id:
        return

    pack = find_pack_by_message(chat_id=chat_id, message_id=int(reply_to_id))
    if not pack or not pack.get("edit_pending_field"):
        return
    field = pack["edit_pending_field"]
    segment_slug = pack["segment_slug"]
    new_value = (msg.get("text") or "").strip()
    if not new_value:
        return
    try:
        apply_pack_edit(segment_slug=segment_slug, field=field, new_value=new_value)
    except Exception as e:
        log.warning("edit.apply.fail seg=%s field=%s err=%s", segment_slug, field, e)
        return
    clear_pack_edit_pending(segment_slug)
    log.info("edit.applied seg=%s field=%s", segment_slug, field)
    _resend_approval(segment_slug)


def main() -> int:
    # Early exit if we're not configured for notifications.
    if not os.environ.get("TELEGRAM_BOT_TOKEN") or not os.environ.get("TELEGRAM_CHAT_ID"):
        log.info("poll.skip no_telegram_config")
        return 0

    offset = get_telegram_offset()
    log.info("poll.start offset=%s", offset)
    updates = get_updates(offset + 1 if offset else 0, timeout=5)
    if not updates:
        log.info("poll.empty")
        return 0

    last_update_id = 0
    for upd in updates:
        try:
            last_update_id = max(last_update_id, int(upd.get("update_id") or 0))
            if "callback_query" in upd:
                _handle_callback(upd["callback_query"])
            elif "message" in upd and upd["message"].get("reply_to_message"):
                _handle_reply(upd["message"])
        except Exception as e:
            log.warning("poll.update.exc update_id=%s err=%s", upd.get("update_id"), e)

    if last_update_id:
        set_telegram_offset(last_update_id)
        log.info("poll.done processed=%s new_offset=%s", len(updates), last_update_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
