"""Telegram digest for Bot 7 runs. Posts a single summary message to
@OminvoCommandBot (same creds as Bot 1)."""
from __future__ import annotations

from typing import Iterable

import sys
from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.logging import get_logger
from shared.telegram import send_message

log = get_logger("bot7.notify")


def send_digest(
    *,
    run_date: str,
    segments_total: int,
    segments_ok: int,
    records_total: int,
    emails_found: int,
    duration_s: int,
    failures: Iterable[tuple[str, str]],
    dry_run: bool,
) -> None:
    fails = list(failures)
    tag = " [DRY]" if dry_run else ""
    lines = [
        f"<b>Bot 7 digest{tag}</b>  <code>{run_date}</code>",
        f"segments: <b>{segments_ok}/{segments_total}</b>",
        f"records:  <b>{records_total}</b>",
        f"emails:   <b>{emails_found}</b>",
        f"duration: {duration_s}s",
    ]
    if fails:
        lines.append("")
        lines.append(f"<b>failures ({len(fails)}):</b>")
        for seg, msg in fails[:10]:
            lines.append(f"• <code>{seg}</code> — {msg[:120]}")
        if len(fails) > 10:
            lines.append(f"…+{len(fails) - 10} more")
    try:
        send_message("\n".join(lines))
    except Exception as e:
        log.warning("bot7.telegram.fail err=%s", e)
