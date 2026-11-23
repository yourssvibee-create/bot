"""Listing phase: populate packs, push to channels, record back.

Called from main.py AFTER csv_writer has produced the per-segment CSVs.
Idempotent — second run of the same day updates existing listings with
the refreshed CSV, doesn't create duplicates.
"""
from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.logging import get_logger

from ..channels.base import ChannelLister, PushRequest
from ..channels.gumroad import GumroadLister
from ..channels.payhip import PayhipLister
from ..channels.whop import WhopLister
from ..config import settings
from ..output.csv_writer import output_root
from ..segments import load_pricing
from ..storage.supabase_client import fetch_ready_packs, update_pack_channel, upsert_pack
from ..types import Business, Segment
from .metadata import build_metadata, write_sample_csv

log = get_logger("bot7.listing.publish")

# One-time packs go to Gumroad + Payhip. Monthly refresh goes to Whop.
# This lives here (not a YAML) because it couples to the channel code.
_ONE_TIME_CHANNELS = ("gumroad", "payhip")
_MONTHLY_CHANNELS = ("whop",)


@dataclass
class PackSpec:
    segment: Segment
    rows: list[Business]
    csv_path: Path


def _active_channels() -> dict[str, ChannelLister]:
    """Return {name: lister} for channels whose creds are set AND which
    are not opt-out via BOT7_CHANNELS_ENABLED."""
    all_: dict[str, ChannelLister] = {
        "gumroad": GumroadLister(),
        "payhip": PayhipLister(),
        "whop": WhopLister(),
    }
    allowed = {x.strip() for x in settings.channels_enabled.split(",") if x.strip()}
    active: dict[str, ChannelLister] = {}
    for name, lister in all_.items():
        if allowed and name not in allowed:
            continue
        if not lister.configured and not settings.dry_run:
            continue
        active[name] = lister
    return active


def populate_packs(packs: list[PackSpec]) -> int:
    """For each segment the run touched, upsert bot7_packs.

    Returns the number of packs set to status='ready' (eligible for push).
    """
    pricing = load_pricing()["tiers"]
    sample_dir = output_root() / "samples"
    ready = 0
    for pack in packs:
        seg = pack.segment
        tier = seg.metro.tier
        row_count = len(pack.rows)

        status = "ready" if row_count >= settings.min_records_for_ready else "draft"

        one_time_price = int(pricing[tier]["one_time"])
        monthly_price = int(pricing[tier]["monthly"])

        try:
            sample_path = write_sample_csv(
                seg, pack.rows, sample_dir, n=settings.sample_pack_rows
            )
        except Exception as e:
            log.warning("populate.sample.fail seg=%s err=%s", seg.slug, e)
            sample_path = Path("")

        one_time_meta = build_metadata(
            seg, pack.rows, price_usd=one_time_price,
            price_kind="one_time", refresh_tier="one_time",
        )
        upsert_pack(
            segment_slug=seg.slug,
            metro_slug=seg.metro.slug,
            vertical_slug=seg.vertical.slug,
            tier=tier,
            price_usd=one_time_price,
            price_kind="one_time",
            refresh_tier="one_time",
            record_count=row_count,
            title=one_time_meta.title,
            description=one_time_meta.description,
            sample_csv_path=str(sample_path),
            status=status,
        )
        # Note: monthly pricing is applied inside list_to_channels at Whop
        # push time — we keep one pack row per segment and let the channel
        # layer pick the right price.
        _ = monthly_price
        if status == "ready":
            ready += 1
    log.info("populate.done packs=%s ready=%s", len(packs), ready)
    return ready


async def _push_one(
    lister: ChannelLister, req: PushRequest
) -> tuple[str, PushRequest, "ListResult"]:
    res = await lister.refresh(req)
    return lister.name, req, res


async def list_to_channels(packs: list[PackSpec]) -> dict[str, int]:
    """Push every ready pack to every active channel, in parallel.

    Returns a per-channel {ok, fail} count dict.
    """
    channels = _active_channels()
    if not channels:
        log.info("list.skip no_active_channels")
        return {}

    pricing = load_pricing()["tiers"]
    sample_dir = output_root() / "samples"

    # Current channel state from DB — so we know existing_product_id per
    # (segment, channel) and avoid duplicate create calls.
    seg_slugs = [p.segment.slug for p in packs]
    db_rows = fetch_ready_packs(segment_slugs=seg_slugs) if seg_slugs else []
    db_by_seg = {r["segment_slug"]: r for r in db_rows}

    tasks: list[asyncio.Task] = []
    task_meta: list[tuple[str, str, int]] = []  # (segment_slug, channel, price_cents)

    for pack in packs:
        seg = pack.segment
        if len(pack.rows) < settings.min_records_for_ready:
            continue
        tier = seg.metro.tier
        db_row = db_by_seg.get(seg.slug, {})

        for ch_name, lister in channels.items():
            if ch_name in _ONE_TIME_CHANNELS:
                price = int(pricing[tier]["one_time"])
                price_kind = "one_time"
                refresh_tier = "one_time"
            elif ch_name in _MONTHLY_CHANNELS:
                price = int(pricing[tier]["monthly"])
                price_kind = "monthly"
                refresh_tier = "monthly"
            else:
                continue

            meta = build_metadata(
                seg, pack.rows, price_usd=price,
                price_kind=price_kind, refresh_tier=refresh_tier,
            )
            existing_id = db_row.get(f"{ch_name}_product_id")

            req = PushRequest(
                segment_slug=seg.slug,
                metadata=meta,
                csv_path=pack.csv_path,
                existing_product_id=existing_id,
            )
            tasks.append(asyncio.create_task(_push_one(lister, req)))
            task_meta.append((seg.slug, ch_name, meta.price_cents))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    counts: dict[str, dict[str, int]] = {n: {"ok": 0, "fail": 0} for n in channels}

    for (seg_slug, ch_name, _), out in zip(task_meta, results):
        if isinstance(out, BaseException):
            log.warning("list.push.exc seg=%s ch=%s err=%s", seg_slug, ch_name, out)
            counts[ch_name]["fail"] += 1
            update_pack_channel(
                segment_slug=seg_slug, channel=ch_name,
                product_id="", product_url="", is_first_listing=False,
                error_note=f"{ch_name}: {out}",
            )
            continue
        _, req, res = out
        if res.ok:
            counts[ch_name]["ok"] += 1
        else:
            counts[ch_name]["fail"] += 1
        update_pack_channel(
            segment_slug=seg_slug, channel=ch_name,
            product_id=res.product_id, product_url=res.product_url,
            is_first_listing=req.existing_product_id is None and res.ok,
            error_note=("" if res.ok else res.error),
        )

    flat = {}
    for n, c in counts.items():
        flat[f"{n}_ok"] = c["ok"]
        flat[f"{n}_fail"] = c["fail"]
    log.info("list.done counts=%s", flat)
    return flat
