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
