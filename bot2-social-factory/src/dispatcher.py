"""Single-platform dispatcher — thin shim over platform_router.run_queue.

Exists for selector-seeding workflows: generate one item for one platform,
dispatch it in dry-run (optionally with --draft-only), see what happens
without polluting the full 7-item nightly batch or producing real posts.

Usage:
    # Generate 1 long-form Medium item, dispatch it as a draft (no Publish click)
    python -m src.dispatcher --platform medium --content-type long \
        --dry-run --draft-only --seed 1121

    # Same but without --dry-run would attempt real publish — do NOT run until
    # the target platform's Publish-now selector is VERIFIED_UI.
"""
from __future__ import annotations

import argparse
import asyncio
import uuid

from shared.config import require_bot_id
from shared.logging import get_logger

from src.content_generator import generate_daily
from src.platform_router import dispatch_item

log = get_logger("bot2.dispatcher")


async def dispatch_one(
    *,
    platform: str,
    content_type: str = "long",
    dry_run: bool = True,
    draft_only: bool = False,
    seed: int | None = None,
) -> None:
    """Generate one item and dispatch it to a single platform.

    --draft-only is advisory: it sets a `draft_only=True` flag on the row.
    Individual adapters decide whether to honor it (Medium's adapter stops
    before the Publish-now click, for example). Adapters that don't know the
    flag just ignore it, so this is a no-op outside adapters that opt in.
    """
    require_bot_id({"bot2"})
    items = await generate_daily(
        dry_run=dry_run,
        seed=seed,
        count=1,
        force_platform=platform,
        force_type=content_type,
    )
    if not items:
        log.warning("dispatcher_no_items")
        return
    it = items[0]
    row = {
        "id": str(uuid.uuid4()),
        "content_type": it.content_type,
        "caption": it.caption,
        "media_urls": list(it.media_urls),
        "target_platforms": [platform],
        "status": "queued",
        "pain_point_id": it.pain_point_id,
        "text_provider": it.text_provider,
        "draft_only": draft_only,
    }
    results = await dispatch_item(row, dry_run=dry_run)
    for r in results:
        log.info("dispatcher_result", extra={
            "platform": r.platform, "status": r.status,
            "external_url": getattr(r, "external_url", None),
            "error": r.error,
        })
        print(f"{r.platform}: status={r.status} url={getattr(r, 'external_url', None)} err={r.error}")


def _main_cli() -> int:
    ap = argparse.ArgumentParser(description="Single-platform dispatcher")
    ap.add_argument("--platform", required=True,
                    help="target platform, e.g. medium / linkedin / x")
    ap.add_argument("--content-type", default="long",
                    help="content_type override (reel / carousel / long / tweet)")
    ap.add_argument("--dry-run", action="store_true",
                    help="skip LLM, skip media, log the dispatch without real post")
    ap.add_argument("--draft-only", action="store_true",
                    help="advisory flag passed to the adapter; adapters that "
                         "support it stop before the final Publish click")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    asyncio.run(dispatch_one(
        platform=args.platform,
        content_type=args.content_type,
        dry_run=args.dry_run,
        draft_only=args.draft_only,
        seed=args.seed,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main_cli())
