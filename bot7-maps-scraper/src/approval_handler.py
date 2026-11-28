"""Bot 7 handler for the shared Telegram approval poll.

Callback contract (set by src/notify/approval.py):
  a:<segment_slug>    approve & publish
  r:<segment_slug>    reject
  et:<segment_slug>   edit title (force_reply prompt)
  ep:<segment_slug>   edit price
  ed:<segment_slug>   edit description

Plus a message handler for replies to those edit prompts.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BOT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(_REPO_ROOT), str(_BOT_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from shared.approval_digest import (
    handle_approve_all, register_aa_approver,
)
from shared.approval_poll_core import register_callback, register_message
from shared.digest_registry import PendingItem, register_pending_provider
from shared.logging import get_logger
from shared.telegram import (
    answer_callback_query,
    send_force_reply,
)

from src.listing.approve import publish_pack
from src.notify.approval import (
    mark_approved, mark_failed, mark_rejected, send_approval_prompt,
)
from src.storage.supabase_client import (
    apply_pack_edit,
    clear_pack_edit_pending,
    find_pack_by_message,
    find_pack_by_segment,
    list_pending_packs,
    mark_pack_published,
    mark_pack_rejected,
    set_pack_approval_message,
    set_pack_edit_pending,
)

log = get_logger("bot7.approval_handler")

_FIELD_BY_OP = {"et": "title", "ep": "price", "ed": "description"}
_FIELD_LABEL = {"title": "title", "price": "price (USD)", "description": "description"}
_CB_RE = re.compile(r"^(a|r|et|ep|ed):(.+)$")


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


def _on_callback(cq: dict[str, Any]) -> None:
    data = cq.get("data") or ""
    m = _CB_RE.match(data)
    cq_id = cq.get("id") or ""
    if not m:
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


def _on_reply(msg: dict[str, Any]) -> bool:
    """Return True iff this message was an answer to one of bot7's
    edit prompts. Lets bot1/bot2 handlers see non-bot7 messages."""
    reply_to = msg.get("reply_to_message") or {}
    reply_to_id = reply_to.get("message_id")
    chat_id = (msg.get("chat") or {}).get("id") or ""
    if not reply_to_id:
        return False
    pack = find_pack_by_message(chat_id=chat_id, message_id=int(reply_to_id))
    if not pack or not pack.get("edit_pending_field"):
        return False
    field = pack["edit_pending_field"]
    segment_slug = pack["segment_slug"]
    new_value = (msg.get("text") or "").strip()
    if not new_value:
        return True  # claimed but empty — swallow
    try:
        apply_pack_edit(segment_slug=segment_slug, field=field, new_value=new_value)
    except Exception as e:
        log.warning("edit.apply.fail seg=%s field=%s err=%s", segment_slug, field, e)
        return True
    clear_pack_edit_pending(segment_slug)
    log.info("edit.applied seg=%s field=%s", segment_slug, field)
    _resend_approval(segment_slug)
    return True


for _op in ("a", "r", "et", "ep", "ed"):
    register_callback(f"{_op}:", _on_callback)
register_message(_on_reply)


def _bot7_pending_provider() -> list[PendingItem]:
    out: list[PendingItem] = []
    for row in list_pending_packs():
        seg = row.get("segment_slug") or ""
        if not seg:
            continue
        cents = int(row.get("price_cents") or 0)
        price_usd = cents // 100 if cents else 0
        kind = row.get("price_kind") or "one_time"
        recs = int(row.get("record_count") or 0)
        out.append(PendingItem(
            bot="bot7",
            item_id=seg,
            title=(row.get("title") or seg)[:120],
            subtitle=f"${price_usd} {kind} · {recs} leads",
            approve_cb=f"a:{seg}",
            reject_cb=f"r:{seg}",
            edit_cb=f"et:{seg}",
        ))
    return out


def _bot7_aa_approve(item_id: str) -> bool:
    """Approve-all helper: publish one bot7 pack by segment_slug."""
    pack = find_pack_by_segment(item_id)
    if not pack:
        return False
    res = publish_pack(
        gumroad_pid=pack.get("gumroad_product_id") or "",
        whop_pid=pack.get("whop_product_id") or "",
    )
    if res.gumroad_ok and res.whop_ok:
        mark_pack_published(
            segment_slug=item_id, approved_by="approve_all",
            gumroad_live_url=res.gumroad_url, whop_live_url=res.whop_url,
        )
        return True
    mark_pack_published(
        segment_slug=item_id, approved_by="approve_all",
        publish_error=res.error or "partial publish",
    )
    return False


register_pending_provider(_bot7_pending_provider)
register_aa_approver("bot7", _bot7_aa_approve)
register_callback("aa:", handle_approve_all)
