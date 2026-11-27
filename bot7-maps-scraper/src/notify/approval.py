"""Per-segment approval prompt. Sent to Telegram after a nightly run
successfully drafts a product on each channel. The operator taps one
of:

  ✅ Approve & Publish   publish on gumroad + whop immediately
  ✏️ Edit title / price / description   force_reply prompt → edit
  ❌ Reject              leave the drafts hidden; no publish

Callback data format (packed small because Telegram caps at 64 bytes):
  a:<segment_slug>       approve
  et:<segment_slug>      edit title
  ep:<segment_slug>      edit price
  ed:<segment_slug>      edit description
  r:<segment_slug>       reject

The segment slug is unique and short (e.g. `nyc__restaurants`).
Reject is passive — no API call, just marks the pack row so the
operator can see what they skipped.
"""
from __future__ import annotations

import html
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.logging import get_logger
from shared.telegram import edit_message_text, send_with_keyboard

log = get_logger("bot7.notify.approval")


def _keyboard(segment_slug: str) -> list[list[dict[str, str]]]:
    return [
        [{"text": "✅ Approve & Publish", "callback_data": f"a:{segment_slug}"}],
        [
            {"text": "✏️ Edit title",    "callback_data": f"et:{segment_slug}"},
            {"text": "✏️ Edit price",    "callback_data": f"ep:{segment_slug}"},
        ],
        [
            {"text": "✏️ Edit description", "callback_data": f"ed:{segment_slug}"},
        ],
        [{"text": "❌ Reject", "callback_data": f"r:{segment_slug}"}],
    ]


def _format_body(
    *,
    segment_slug: str,
    title: str,
    price_usd: int,
    price_kind: str,
    record_count: int,
    emails_found: int,
    gumroad_url: str,
    whop_url: str,
    download_url: str,
) -> str:
    esc = html.escape
    lines = [
        f"📦 <b>{esc(segment_slug)}</b>",
        f"<b>Title:</b> {esc(title)}",
        f"<b>Price:</b> ${price_usd} ({price_kind})",
        f"<b>Records:</b> {record_count}   <b>Emails:</b> {emails_found}",
    ]
    if download_url:
        lines.append(f'<a href="{esc(download_url)}">📄 CSV (90-day signed)</a>')
    if gumroad_url:
        lines.append(f'<a href="{esc(gumroad_url)}">Gumroad draft</a>')
    if whop_url:
        lines.append(f'<a href="{esc(whop_url)}">Whop draft</a>')
    lines.append("")
    lines.append("Tap below to approve, edit, or reject.")
    return "\n".join(lines)


def send_approval_prompt(
    *,
    segment_slug: str,
    title: str,
    price_usd: int,
    price_kind: str,
    record_count: int,
    emails_found: int,
    gumroad_url: str,
    whop_url: str,
    download_url: str,
) -> int | None:
    """Post a per-segment approval message. Returns the Telegram
    message_id on success, or None on failure."""
    text = _format_body(
        segment_slug=segment_slug, title=title, price_usd=price_usd,
        price_kind=price_kind, record_count=record_count,
        emails_found=emails_found, gumroad_url=gumroad_url,
        whop_url=whop_url, download_url=download_url,
    )
    try:
        body = send_with_keyboard(text, keyboard=_keyboard(segment_slug))
    except Exception as e:
        log.warning("approval.send.fail seg=%s err=%s", segment_slug, e)
        return None
    msg_id = ((body.get("result") or {}).get("message_id"))
    log.info("approval.send.ok seg=%s msg_id=%s", segment_slug, msg_id)
    return int(msg_id) if msg_id else None


def mark_approved(
    *, chat_id: str | int, message_id: int, segment_slug: str,
    gumroad_live_url: str, whop_live_url: str,
) -> None:
    """Replace the keyboard+body with a 'live' confirmation."""
    text = (
        f"✅ <b>Live</b> — {html.escape(segment_slug)}\n"
        f'<a href="{html.escape(gumroad_live_url)}">Gumroad</a>  ·  '
        f'<a href="{html.escape(whop_live_url)}">Whop</a>'
    )
    try:
        edit_message_text(chat_id=chat_id, message_id=message_id, text=text, keyboard=[])
    except Exception as e:
        log.warning("approval.mark_live.fail seg=%s err=%s", segment_slug, e)


def mark_rejected(*, chat_id: str | int, message_id: int, segment_slug: str) -> None:
    try:
        edit_message_text(
            chat_id=chat_id, message_id=message_id,
            text=f"❌ <b>Rejected</b> — {html.escape(segment_slug)}\nDrafts left hidden.",
            keyboard=[],
        )
    except Exception as e:
        log.warning("approval.mark_reject.fail seg=%s err=%s", segment_slug, e)


def mark_failed(*, chat_id: str | int, message_id: int, segment_slug: str, err: str) -> None:
    try:
        edit_message_text(
            chat_id=chat_id, message_id=message_id,
            text=(
                f"⚠️ <b>Publish failed</b> — {html.escape(segment_slug)}\n"
                f"<code>{html.escape(err[:400])}</code>"
            ),
            keyboard=[
                [{"text": "🔁 Retry", "callback_data": f"a:{segment_slug}"}],
                [{"text": "❌ Cancel", "callback_data": f"r:{segment_slug}"}],
            ],
        )
    except Exception as e:
        log.warning("approval.mark_fail.fail seg=%s err=%s", segment_slug, e)
