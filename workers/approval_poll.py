"""Repo-wide Telegram approval poll entrypoint.

Imports every bot's `approval_handler` module (which registers its
callback + message handlers against shared.approval_poll_core at
import time), then runs one poll pass against the shared Telegram
bot.

Offset storage: `bot7_telegram_offset` is reused as the shared
cursor table. The name predates the rollout to Bot 1/Bot 2 but the
semantics are unchanged (single-row, last_offset bigint).

Env vars required:
  TELEGRAM_BOT_TOKEN   shared bot token (see gh secrets)
  TELEGRAM_CHAT_ID     shared chat id
  DATABASE_URL         Supabase Postgres
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Side-effect imports: each bot's approval_handler registers its
# callbacks and message handlers with shared.approval_poll_core on
# import. Order matters only for message handlers (bot7's reply
# handler is checked first because it's the most specific).
#
# Each bot's src/config.py pins require_bot_id({"botN"}), so we set
# BOT_ID to that bot's expected value before each import. Python
# caches the module after first import, so the pin only runs once;
# BOT_ID can safely move on to the next bot after the import returns.
_HANDLER_MODULES = [
    ("bot7-maps-scraper", "src.approval_handler", "bot7"),
    ("bot1-intent-hijacker", "src.approval_handler", "bot1"),
    ("bot2-social-factory", "src.approval_handler", "bot2"),
]

for _pkg_root, _modpath, _bot_id in _HANDLER_MODULES:
    pkg_dir = _REPO_ROOT / _pkg_root
    if str(pkg_dir) not in sys.path:
        sys.path.insert(0, str(pkg_dir))
    os.environ["BOT_ID"] = _bot_id
    try:
        __import__(_modpath)
    except Exception as e:
        # One bot failing to load must not block the others. The poll
        # worker is a shared runtime; we log and continue.
        print(f"[approval_poll] WARNING: {_pkg_root}.{_modpath} failed to load: {e}", file=sys.stderr)
# After all handlers load, mark the process as 'shared' again so the
# shared/approval_poll_core logging is unambiguous. Nothing further
# checks BOT_ID at module import time once handlers are registered.
os.environ["BOT_ID"] = "shared"

from shared.approval_poll_core import poll_once  # noqa: E402
from shared.logging import get_logger  # noqa: E402

log = get_logger("approval_poll")


def _get_offset() -> int:
    import psycopg
    with psycopg.connect(
        os.environ["DATABASE_URL"], autocommit=True, prepare_threshold=None,
    ) as c, c.cursor() as cur:
        cur.execute("select last_offset from bot7_telegram_offset where id = 1")
        row = cur.fetchone()
        return int(row[0]) if row else 0


def _set_offset(offset: int) -> None:
    import psycopg
    with psycopg.connect(
        os.environ["DATABASE_URL"], autocommit=True, prepare_threshold=None,
    ) as c, c.cursor() as cur:
        cur.execute(
            "update bot7_telegram_offset set last_offset = %s, updated_at = now() where id = 1",
            (int(offset),),
        )


def main() -> int:
    if not os.environ.get("TELEGRAM_BOT_TOKEN") or not os.environ.get("TELEGRAM_CHAT_ID"):
        log.info("poll.skip no_telegram_config")
        return 0
    poll_once(get_offset=_get_offset, set_offset=_set_offset)
    return 0


if __name__ == "__main__":
    sys.exit(main())
