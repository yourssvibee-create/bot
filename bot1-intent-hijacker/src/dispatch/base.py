"""Shared Playwright helpers for dispatch: polite delays, screenshot on fail."""
from __future__ import annotations

import asyncio
import random
from pathlib import Path
from typing import Any

from playwright.async_api import Page

from shared.db import execute
from shared.logging import get_logger

log = get_logger("bot1.dispatch.base")

SHOT_DIR = Path.home() / ".bot_sessions" / "bot1" / "shots"
SHOT_DIR.mkdir(parents=True, exist_ok=True)


async def polite(min_s: float = 1.5, max_s: float = 4.0) -> None:
    await asyncio.sleep(random.uniform(min_s, max_s))


async def screenshot_fail(page: Page, tag: str) -> str | None:
    try:
        path = SHOT_DIR / f"{tag}.png"
        await page.screenshot(path=str(path), full_page=True)
        return str(path)
    except Exception as e:
        log.warning("shot_fail", extra={"err": str(e)})
        return None


def record(kind: str, *, target: str | None, channel: str | None,
           status: str, detail: dict[str, Any] | None = None) -> None:
    try:
        execute(
            "insert into dispatch_log (bot, kind, target, channel, status, detail) "
            "values (%s, %s, %s, %s, %s, %s::jsonb)",
            ("bot1", kind, target, channel, status,
             None if detail is None else _json(detail)),
        )
    except Exception as e:
        log.warning("dispatch_log_write_failed", extra={"err": str(e)})


def _json(o: Any) -> str:
    import json
    return json.dumps(o, default=str, ensure_ascii=False)
