"""Vercel Python serverless function: Bot 2 Telegram slash-command webhook.

Routes /bot2_* commands to handlers in src/telegram_approve.py and replies via
Telegram sendMessage. Ignores non-command messages and non-matching commands.

Set the Telegram bot's webhook to:
  https://<bot2-vercel-domain>/api/telegram_webhook
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

# Make sibling `shared/` and this bot's `src/` importable on Vercel.
_HERE = Path(__file__).resolve()
_BOT_ROOT = _HERE.parents[1]
_REPO_ROOT = _HERE.parents[2]
for p in (str(_REPO_ROOT), str(_BOT_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from shared.logging import get_logger  # noqa: E402
from shared.telegram import send_message  # noqa: E402

from src.telegram_approve import dispatch_command  # noqa: E402

log = get_logger("bot2.telegram_webhook")


def _handle(payload: dict) -> dict:
    msg = payload.get("message") or payload.get("edited_message") or {}
    text = (msg.get("text") or "").strip()
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")

    if not text.startswith("/"):
        return {"ok": True, "ignored": "not_a_command"}

    result = dispatch_command(text)
    reply = result.get("reply") or "(no reply)"
    if chat_id:
        try:
            send_message(reply)
        except Exception as e:
            log.warning("tg_send_fail", extra={"err": str(e)[:200]})
    log.info("bot2_cmd_handled", extra={"text": text[:60], "chat_id": chat_id})
    return {"ok": True, "reply": reply}


class handler(BaseHTTPRequestHandler):  # noqa: N801 — Vercel expects lowercase
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"ok": false, "reason": "bad_json"}')
            return

        body = _handle(payload)
        data = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok": true, "service": "bot2-telegram-webhook"}')
