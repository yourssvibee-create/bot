"""Repo-wide morning approval digest entrypoint.

Fires once a day (09:00 IST / 03:30 UTC via approval-digest.yml).
Imports every bot's approval_handler (which registers its pending-items
provider + aa_approve function at import time), then assembles ONE
Telegram card listing every pending item across bot1/bot2/bot7 with
per-item buttons + a single [✅ Approve All] button.

Per-item taps route through the existing shared approval-poll worker's
callbacks (a:/r:/et:/...), which runs every 5 min on its own schedule.
The [Approve All] tap uses callback prefix 'aa:' which is also handled
by that same shared poll worker.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_HANDLER_MODULES = [
    ("bot7-maps-scraper",  "src.approval_handler", "bot7"),
    ("bot1-intent-hijacker", "src.approval_handler", "bot1"),
    ("bot2-social-factory",  "src.approval_handler", "bot2"),
]

for _pkg_root, _modpath, _bot_id in _HANDLER_MODULES:
    pkg_dir = _REPO_ROOT / _pkg_root
    if str(pkg_dir) not in sys.path:
        sys.path.insert(0, str(pkg_dir))
    os.environ["BOT_ID"] = _bot_id
    try:
        __import__(_modpath)
    except Exception as e:
        print(
            f"[approval_digest] WARNING: {_pkg_root}.{_modpath} failed to load: {e}",
            file=sys.stderr,
        )
os.environ["BOT_ID"] = "shared"

from shared.approval_digest import send_morning_digest  # noqa: E402
from shared.logging import get_logger  # noqa: E402

log = get_logger("approval_digest")


def main() -> int:
    if not os.environ.get("TELEGRAM_BOT_TOKEN") or not os.environ.get("TELEGRAM_CHAT_ID"):
        log.info("digest.skip no_telegram_config")
        return 0
    send_morning_digest()
    return 0


if __name__ == "__main__":
    sys.exit(main())
