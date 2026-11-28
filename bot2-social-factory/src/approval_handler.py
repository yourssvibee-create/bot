"""Bot 2 handler for the shared Telegram approval poll.

Contract: text messages whose body starts with a slash and the token
matches a key in `COMMANDS` (currently /bot2_approve_today,
/bot2_pause, /bot2_stats — see src/telegram_approve.py).

The shared poller calls every registered message handler until one
returns True. This handler returns True iff the first token is a
bot2_ command we recognise; otherwise False so bot1/bot7 handlers
can still see the message.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BOT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(_REPO_ROOT), str(_BOT_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from shared.approval_poll_core import register_message
from shared.logging import get_logger
from shared.telegram import send_message

from src.telegram_approve import COMMANDS, dispatch_command

log = get_logger("bot2.approval_handler")


def _on_message(msg: dict[str, Any]) -> bool:
    text = (msg.get("text") or "").strip()
    if not text.startswith("/"):
        return False
    cmd = text.lstrip("/").split("@", 1)[0].split()[0] if text else ""
    if cmd not in COMMANDS:
        return False
    try:
        result = dispatch_command(text)
    except Exception as e:
        log.warning("bot2.dispatch.fail cmd=%s err=%s", cmd, e)
        send_message(f"bot2 cmd error: {e}")
        return True
    reply = result.get("reply") or "(no reply)"
    try:
        send_message(reply)
    except Exception as e:
        log.warning("bot2.reply.fail cmd=%s err=%s", cmd, e)
    log.info("bot2.cmd cmd=%s", cmd)
    return True


register_message(_on_message)
