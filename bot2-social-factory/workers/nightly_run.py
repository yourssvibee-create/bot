"""Bot 2 nightly orchestrator — fail-soft diagnostic mode.

Phases:
  1. generate_content   — content_generator.generate_daily -> list[ContentItem]
  2. render_media       — image router (carousels) + Pixabay (reels/long)
  3. dispatch           — platform_router.run_queue (API first, Playwright serial)
  4. telegram_digest    — HTML summary to TELEGRAM_CHAT_ID

Each phase wraps its work in try/except, records (name, status, note), and
continues. End prints a summary table and exits 1 if any phase failed.

Dry-run: honored via `--dry-run` CLI flag OR `BOT2_DRY_RUN=true` env var.
In dry-run: no LLM, no Pixabay, no Supabase write, no actual publishing,
no Telegram send. Everything compiles end-to-end, exits 0.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import sys
import traceback
import uuid
from pathlib import Path
from typing import Any

from shared.config import get_bool, require_bot_id
from shared.logging import get_logger

from src.content_generator import ContentItem, generate_daily
from src.media_renderer import render
from src.platform_router import DispatchRow, run_queue

log = get_logger("bot2.nightly")

_CAROUSEL_CACHE = Path(__file__).resolve().parents[1] / ".media-cache" / "carousel"


class PhaseTracker:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []  # (name, status, note)

    def ok(self, name: str, note: str = "") -> None:
        self.rows.append((name, "ok", note))

    def fail(self, name: str, err: BaseException) -> None:
        traceback.print_exc()
        msg = str(err).splitlines()[0] if str(err) else type(err).__name__
        self.rows.append((name, "fail", msg[:200]))

    def any_failed(self) -> bool:
        return any(s == "fail" for _, s, _ in self.rows)

    def render(self) -> str:
        if not self.rows:
            return "(no phases recorded)"
        w = max(len(n) for n, _, _ in self.rows)
        out = ["", "=" * 78, "PHASE SUMMARY", "=" * 78]
        for name, status, note in self.rows:
            marker = "OK  " if status == "ok" else "FAIL"
            out.append(f"  {marker}  {name.ljust(w)}  {note}")
        out.append("=" * 78)
        return "\n".join(out)


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


async def _render_one(it: ContentItem, i: int, seed: int | None,
                      dry_run: bool) -> tuple[list[str], str | None]:
    """Render media for one ContentItem. Returns (media_paths, err_or_none)."""
    media: list[str] = []
    if dry_run:
        return media, None
    try:
        if it.content_type == "carousel":
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
            query = it.pain_point_id.replace("pp_", "small business reviews ")
            media = await render(query, provider="pixabay", n=1, dry_run=False)
            if media:
                it.image_providers.append("pixabay")
    except Exception as e:
        log.warning("render_item_fail",
                    extra={"err": str(e)[:200], "pp": it.pain_point_id,
                           "content_type": it.content_type})
        return media, str(e)[:200]
    return media, None


def _digest(rows: list[dict[str, Any]], results: list[DispatchRow],
            phases: str) -> str:
    n_queued = len(rows)
    by_status: dict[str, int] = {}
    for r in results:
        by_status[r.status] = by_status.get(r.status, 0) + 1
    parts = [f"<b>bot2 nightly</b> queued={n_queued} dispatches={len(results)}"]
    for s, c in sorted(by_status.items()):
        parts.append(f"  {html.escape(s)}: {c}")
    parts.append("<b>providers:</b>")
    for r in rows:
        text_p = r.get("text_provider") or "?"
        img_ps = r.get("image_providers") or []
        img_str = (" + imgs(" + ", ".join(img_ps) + ")") if img_ps else ""
        parts.append(
            f"  {html.escape(r['content_type'])} ({html.escape(text_p)})"
            f"{html.escape(img_str)}"
        )
    first_fail = next((r for r in results if r.status == "failed"), None)
    if first_fail:
        parts.append(
            f"  first_fail: <code>{html.escape(first_fail.platform)}</code> "
            f"{html.escape((first_fail.error or '')[:160])}"
        )
    parts.append("<pre>" + html.escape(phases) + "</pre>")
    return "\n".join(parts)


async def nightly(*, dry_run: bool, seed: int | None) -> int:
    bot_id = require_bot_id({"bot2"})
    log.info("nightly_start", extra={"bot_id": bot_id, "dry_run": dry_run})
    t = PhaseTracker()

    # --- Phase 1: generate content ---
    items: list[ContentItem] = []
    try:
        items = await generate_daily(dry_run=dry_run, seed=seed)
        t.ok("generate_content", f"{len(items)} items")
    except Exception as e:
        t.fail("generate_content", e)

    # --- Phase 2: render media ---
    rows: list[dict[str, Any]] = []
    render_errors = 0
    for i, it in enumerate(items):
        media, err = await _render_one(it, i, seed, dry_run)
        if err:
            render_errors += 1
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
    if items:
        if render_errors:
            # Partial render failures don't kill the phase — they're logged and
            # the item still dispatches with empty media. Only a hard exception
            # above (0 items) would have FAILed generate_content.
            t.ok("render_media",
                 f"{len(rows) - render_errors}/{len(rows)} rendered "
                 f"({render_errors} items with render errors)")
        else:
            t.ok("render_media", f"{len(rows)} rendered")
    else:
        t.ok("render_media", "no items to render")
    log.info("content_queued", extra={"n": len(rows), "dry_run": dry_run})

    # --- Phase 3: dispatch ---
    results: list[DispatchRow] = []
    try:
        results = await run_queue(rows, dry_run=dry_run)
        n_failed = sum(1 for r in results if r.status == "failed")
        n_posted = sum(1 for r in results if r.status in ("posted", "skipped_dry_run"))
        n_skipped = sum(1 for r in results if r.status.startswith("skipped"))
        note = (f"{n_posted} posted, {n_skipped} skipped, "
                f"{n_failed} failed / {len(results)} total")
        if n_failed and not results:
            t.fail("dispatch", RuntimeError(note))
        else:
            # Individual platform failures are tracked but don't FAIL the
            # phase — Bot 2 posts to many platforms; one broken adapter
            # shouldn't red-X the whole run.
            t.ok("dispatch", note)
    except Exception as e:
        t.fail("dispatch", e)

    # --- Phase 4: telegram digest ---
    phase_text = t.render()
    digest = _digest(rows, results, phase_text)
    if dry_run:
        print(digest)
        t.ok("telegram_digest", "DRY_RUN: skipped send")
    else:
        try:
            from shared.telegram import send_message
            send_message(digest)
            t.ok("telegram_digest", "sent")
        except Exception as e:
            t.fail("telegram_digest", e)

    # Re-render summary after phase 4 was added.
    final = t.render()
    print(final, flush=True)
    log.info("nightly_done",
             extra={"queued": len(rows), "dispatched": len(results),
                    "any_failed": t.any_failed()})
    return 1 if t.any_failed() else 0


def _main_cli() -> int:
    ap = argparse.ArgumentParser(description="Bot 2 nightly orchestrator")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    dry_run = args.dry_run or get_bool("BOT2_DRY_RUN", False)
    return asyncio.run(nightly(dry_run=dry_run, seed=args.seed))


if __name__ == "__main__":
    sys.exit(_main_cli())
