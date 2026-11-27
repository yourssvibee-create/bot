"""Telegram helpers: plain send + approval inline-button send.

send_approval(callback_data=lead_id) attaches Approve/Reject buttons whose
callback_data is `approve:<lead_id>` / `reject:<lead_id>`.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import get
from .logging import get_logger

log = get_logger("telegram")


def _token() -> str:
    return get("TELEGRAM_BOT_TOKEN", required=True)  # type: ignore[return-value]


def _chat() -> str:
    return get("TELEGRAM_CHAT_ID", required=True)  # type: ignore[return-value]


def _url(method: str) -> str:
    return f"https://api.telegram.org/bot{_token()}/{method}"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1.5, min=1, max=10))
def send_message(text: str, *, parse_mode: str = "HTML", disable_preview: bool = True) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chat_id": _chat(),
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": disable_preview,
    }
    r = httpx.post(_url("sendMessage"), json=payload, timeout=20)
    if r.status_code >= 400:
        # Surface Telegram's error description so a bad parse_mode / entity
        # doesn't just show up as a bare HTTPStatusError upstream.
        log.warning("telegram.send.http status=%s body=%s", r.status_code, r.text[:400])
    r.raise_for_status()
    return r.json()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1.5, min=1, max=10))
def send_approval(text: str, *, callback_data: str, parse_mode: str = "HTML") -> dict[str, Any]:
    """Send a message with inline Approve / Reject buttons."""
    keyboard = {
        "inline_keyboard": [[
            {"text": "✅ Approve", "callback_data": f"approve:{callback_data}"},
            {"text": "❌ Reject",  "callback_data": f"reject:{callback_data}"},
        ]]
    }
    payload: dict[str, Any] = {
        "chat_id": _chat(),
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
        "reply_markup": keyboard,
    }
    r = httpx.post(_url("sendMessage"), json=payload, timeout=20)
    r.raise_for_status()
    return r.json()


def answer_callback_query(callback_query_id: str, text: Optional[str] = None) -> None:
    payload: dict[str, Any] = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    try:
        httpx.post(_url("answerCallbackQuery"), json=payload, timeout=10)
    except Exception as e:
        log.warning("telegram_ack_failed", extra={"err": str(e)})


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1.5, min=1, max=10))
def send_with_keyboard(
    text: str,
    *,
    keyboard: list[list[dict[str, str]]],
    parse_mode: str = "HTML",
) -> dict[str, Any]:
    """Send a message with an arbitrary inline keyboard (rows of
    {text, callback_data} buttons)."""
    payload: dict[str, Any] = {
        "chat_id": _chat(),
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
        "reply_markup": {"inline_keyboard": keyboard},
    }
    r = httpx.post(_url("sendMessage"), json=payload, timeout=20)
    if r.status_code >= 400:
        log.warning("telegram.send.http status=%s body=%s", r.status_code, r.text[:400])
    r.raise_for_status()
    return r.json()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1.5, min=1, max=10))
def send_force_reply(text: str, *, parse_mode: str = "HTML") -> dict[str, Any]:
    """Send a message that forces the user's next tap to be a reply to it.
    Used by the approve/edit flow — the poller matches the reply back to
    the pending edit via (chat_id, message_id)."""
    payload: dict[str, Any] = {
        "chat_id": _chat(),
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
        "reply_markup": {"force_reply": True, "selective": False},
    }
    r = httpx.post(_url("sendMessage"), json=payload, timeout=20)
    if r.status_code >= 400:
        log.warning("telegram.send.http status=%s body=%s", r.status_code, r.text[:400])
    r.raise_for_status()
    return r.json()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1.5, min=1, max=10))
def edit_message_text(
    *,
    chat_id: str | int,
    message_id: int,
    text: str,
    keyboard: Optional[list[list[dict[str, str]]]] = None,
    parse_mode: str = "HTML",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if keyboard is not None:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    r = httpx.post(_url("editMessageText"), json=payload, timeout=20)
    if r.status_code >= 400:
        log.warning("telegram.edit.http status=%s body=%s", r.status_code, r.text[:400])
    r.raise_for_status()
    return r.json()


def get_updates(offset: int, *, timeout: int = 10) -> list[dict[str, Any]]:
    """Long-poll Telegram for updates since `offset`. Returns the raw
    `result` array; the caller is responsible for computing the next
    offset as max(update_id)+1."""
    payload: dict[str, Any] = {
        "offset": offset,
        "timeout": timeout,
        "allowed_updates": ["message", "callback_query"],
    }
    try:
        r = httpx.post(_url("getUpdates"), json=payload, timeout=timeout + 5)
    except httpx.HTTPError as e:
        log.warning("telegram.poll.http err=%s", e)
        return []
    if r.status_code >= 400:
        log.warning("telegram.poll.http status=%s body=%s", r.status_code, r.text[:400])
        return []
    body = r.json() if r.content else {}
    if not body.get("ok"):
        log.warning("telegram.poll.not_ok body=%s", str(body)[:400])
        return []
    return body.get("result") or []
