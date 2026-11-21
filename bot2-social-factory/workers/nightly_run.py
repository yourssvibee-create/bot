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
import hashlib
import html
import uuid
from pathlib import Path
from typing import Any

from shared.config import get, require_bot_id
from shared.logging import get_logger

from src.content_generator import ContentItem, generate_daily
from src.media_renderer import render
from src.platform_router import DispatchRow, run_queue

log = get_logger("bot2.nightly")

_CAROUSEL_CACHE = Path(__file__).resolve().parents[1] / ".media-cache" / "carousel"


def _carousel_prompts(caption: str) -> list[str]:
    """Split a carousel caption into per-slide image prompts.

    content_generator emits 5 slides formatted as 'SLIDE N: <text>'; we use
    each slide's text as the image prompt. Falls back to one prompt per line
    if the SLIDE N format is missing.
    """
    lines = [l.strip() for l in caption.splitlines() if l.strip()]
    prompts: list[str] = []
    for l in lines:
        if l.lower().startswith("slide"):
            parts = l.split(":", 1)
            if len(parts) == 2 and parts[1].strip():
                prompts.append(parts[1].strip())
    return prompts or lines[:5]


async def _build_queue_rows(dry_run: bool, seed: int | None,
                            items: list[ContentItem]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for i, it in enumerate(items):
        media: list[str] = []
        if dry_run:
            pass
        elif it.content_type == "carousel":
            # Image router — rotates Gemini / HF FLUX / NVIDIA FLUX per slide.
            from src.router.image_router import generate_carousel
            item_seed = (seed or 0) + i
            prompts = _carousel_prompts(it.caption)
            imgs = await generate_carousel(prompts, seed=item_seed)
            _CAROUSEL_CACHE.mkdir(parents=True, exist_ok=True)
            for j, (png, provider) in enumerate(imgs):
                h = hashlib.sha1(f"{item_seed}:{j}".encode()).hexdigest()[:12]
                p = _CAROUSEL_CACHE / f"{h}.png"
                p.write_bytes(png)
                media.append(str(p))
                it.image_providers.append(provider)
        elif it.content_type in ("reel", "long"):
            # Single still from Pixabay, keyed off pain point.
            query = it.pain_point_id.replace("pp_", "small business reviews ")
            try:
                media = await render(query, provider="pixabay", n=1, dry_run=False)
                if media:
                    it.image_providers.append("pixabay")
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
            "text_provider": it.text_provider,
            "image_providers": list(it.image_providers),
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
    # Provider attribution per queued item — "which AI wrote / drew this."
    parts.append("<b>providers:</b>")
    for r in rows:
        text_p = r.get("text_provider") or "?"
        img_ps = r.get("image_providers") or []
        img_str = (" + imgs(" + ", ".join(img_ps) + ")") if img_ps else ""
        parts.append(
            f"  {html.escape(r['content_type'])} ({html.escape(text_p)})"
            f"{html.escape(img_str)}"
        )
    # Sample first failure if any
    first_fail = next((r for r in results if r.status == "failed"), None)
    if first_fail:
        parts.append(f"  first_fail: <code>{html.escape(first_fail.platform)}</code> "
                     f"{html.escape((first_fail.error or '')[:160])}")
    return "\n".join(parts)


async def nightly(*, dry_run: bool, seed: int | None) -> int:
    bot_id = require_bot_id({"bot2"})
    log.info("nightly_start", extra={"bot_id": bot_id, "dry_run": dry_run})

    items = await generate_daily(dry_run=dry_run, seed=seed)
    rows = await _build_queue_rows(dry_run=dry_run, seed=seed, items=items)
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
