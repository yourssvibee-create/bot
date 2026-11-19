"""Vercel Python serverless function: Telegram approval webhook.

Receives inline-button callbacks whose data is `approve:<drafted_id>` or
`reject:<drafted_id>` and flips the row in `replies_drafted`.

Deploy on Vercel: Python runtime picks this file up as /api/telegram_webhook.
Set the Telegram bot webhook to:
  https://<your-vercel-domain>/api/telegram_webhook
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

# Make sibling `shared/` importable on Vercel.
_HERE = Path(__file__).resolve()
_REPO_ROOT = _HERE.parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.db import execute  # noqa: E402
from shared.logging import get_logger  # noqa: E402
from shared.telegram import answer_callback_query  # noqa: E402

log = get_logger("bot1.telegram_webhook")


def _handle(payload: dict) -> dict:
    cq = payload.get("callback_query")
    if not cq:
        return {"ok": True, "ignored": "no_callback_query"}
    data = (cq.get("data") or "").strip()
    cq_id = cq.get("id")
    if ":" not in data:
        answer_callback_query(cq_id, "bad data")
        return {"ok": False, "reason": "bad_data"}
    action, drafted_id = data.split(":", 1)
    if action not in ("approve", "reject"):
        answer_callback_query(cq_id, "unknown action")
        return {"ok": False, "reason": "unknown_action"}

    new_status = "approved" if action == "approve" else "rejected"
    execute(
        "update replies_drafted set status=%s, decided_at=now() "
        "where id=%s and status='pending'",
        (new_status, drafted_id),
    )
    answer_callback_query(cq_id, f"marked {new_status}")
    log.info("approval_decided", extra={"drafted_id": drafted_id, "status": new_status})
    return {"ok": True, "status": new_status, "drafted_id": drafted_id}


class handler(BaseHTTPRequestHandler):  # noqa: N801 — Vercel expects lowercase
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception as e:
            self._respond(400, {"ok": False, "err": str(e)})
            return
        try:
            out = _handle(payload)
        except Exception as e:
            log.warning("webhook_fail", extra={"err": str(e)})
            self._respond(500, {"ok": False, "err": str(e)})
            return
        self._respond(200, out)

    def do_GET(self):  # noqa: N802 — health check
        self._respond(200, {"ok": True, "service": "bot1-telegram-webhook"})

    def _respond(self, code: int, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
