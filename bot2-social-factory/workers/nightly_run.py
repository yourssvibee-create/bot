"""Bot 2 nightly orchestrator.

Pipeline:
  1. content_generator.generate_daily -> list[ContentItem]
  2. media_renderer.render per item (Pexels for now; stubs for others)
  3. (future) insert each item into bot2_content_queue
  4. platform_router.run_queue dispatches to target_platforms
  5. Telegram digest to TELEGRAM_CHAT_ID

With --dry-run: no LLM, no Pexels, no Supabase write, no actual publishing,
no Telegram send. Everything compiles end-to-end, exits 0.
"""
from __future__ import annotations

import argparse
import asyncio
import html
import uuid
from typing import Any

from shared.config import get, require_bot_id
from shared.logging import get_logger

from src.content_generator import generate_daily
from src.media_renderer import render
from src.platform_router import DispatchRow, run_queue

log = get_logger("bot2.nightly")


async def _build_queue_rows(dry_run: bool, seed: int | None) -> list[dict[str, Any]]:
    items = await generate_daily(dry_run=dry_run, seed=seed)
    rows: list[dict[str, Any]] = []
    for it in items:
        media: list[str] = []
        if it.content_type in ("reel", "carousel", "long") and not dry_run:
            # Pick one query based on pain point for Pexels
            query = it.pain_point_id.replace("pp_", "small business reviews ")
            try:
                media = await render(query, provider="pexels", n=1, dry_run=False)
            except Exception as e:
                log.warning("media_render_fail",
                            extra={"err": str(e)[:200], "pp": it.pain_point_id})
        rows.append({
            "id": str(uuid.uuid4()),
            "content_type": it.content_type,
            "caption": it.caption,
            "media_urls": media or it.media_urls,
            "target_platforms": it.target_platforms,
            "status": "queued",
            "pain_point_id": it.pain_point_id,
        })
    return rows


def _digest(rows: list[dict[str, Any]], results: list[DispatchRow]) -> str:
    n_queued = len(rows)
    by_status: dict[str, int] = {}
    for r in results:
        by_status[r.status] = by_status.get(r.status, 0) + 1
    parts = [f"<b>bot2 nightly</b> queued={n_queued} dispatches={len(results)}"]
    for s, c in sorted(by_status.items()):
        parts.append(f"  {html.escape(s)}: {c}")
    # Sample first failure if any
    first_fail = next((r for r in results if r.status == "failed"), None)
    if first_fail:
        parts.append(f"  first_fail: <code>{html.escape(first_fail.platform)}</code> "
                     f"{html.escape((first_fail.error or '')[:160])}")
    return "\n".join(parts)


async def nightly(*, dry_run: bool, seed: int | None) -> int:
    bot_id = require_bot_id({"bot2"})
    log.info("nightly_start", extra={"bot_id": bot_id, "dry_run": dry_run})

    rows = await _build_queue_rows(dry_run=dry_run, seed=seed)
    log.info("content_queued", extra={"n": len(rows), "dry_run": dry_run})

    results = await run_queue(rows, dry_run=dry_run)

    digest = _digest(rows, results)
    if dry_run:
        print(digest)
        log.info("nightly_dry_done",
                 extra={"queued": len(rows), "dispatched": len(results)})
        return 0

    try:
        from shared.telegram import send_message
        send_message(digest)
    except Exception as e:
        log.warning("telegram_digest_fail", extra={"err": str(e)[:200]})
    log.info("nightly_done", extra={"queued": len(rows), "dispatched": len(results)})
    return 0


def _main_cli() -> int:
    ap = argparse.ArgumentParser(description="Bot 2 nightly orchestrator")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    return asyncio.run(nightly(dry_run=args.dry_run, seed=args.seed))


if __name__ == "__main__":
    raise SystemExit(_main_cli())
