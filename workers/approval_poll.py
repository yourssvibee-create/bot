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
_HANDLER_MODULES = [
    "bot7-maps-scraper.src.approval_handler",
    "bot1-intent-hijacker.src.approval_handler",
    "bot2-social-factory.src.approval_handler",
]

for _mod in _HANDLER_MODULES:
    # The package dir has a hyphen so we add its containing path and
    # import the submodule by its local name.
    pkg_root, _dot, modpath = _mod.partition(".")
    pkg_dir = _REPO_ROOT / pkg_root
    if str(pkg_dir) not in sys.path:
        sys.path.insert(0, str(pkg_dir))
    try:
        __import__(modpath)
    except Exception as e:
        # One bot failing to load must not block the others. The poll
        # worker is a shared runtime; we log and continue.
        print(f"[approval_poll] WARNING: {_mod} failed to load: {e}", file=sys.stderr)

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
