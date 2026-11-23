"""Relist entrypoint for bot7-relist.yml.

Reads every already-listed pack out of bot7_packs, rebuilds the metadata
from the latest CSV on disk, and refreshes all configured channels. This
is the actual delivery mechanism for Whop's monthly-refresh tier.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_BOT_ROOT = Path(__file__).resolve().parents[1]
if str(_BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(_BOT_ROOT))

from shared.logging import configure, get_logger

configure()
log = get_logger("bot7.relist")


def main() -> int:
    from src.config import settings  # noqa: F401 (ensures BOT_ID guard runs)
    from src.listing.publish import PackSpec, list_to_channels
    from src.output.csv_writer import output_root
    from src.segments import load_metros, load_verticals
    from src.storage.supabase_client import fetch_ready_packs
    from src.types import Business, Segment

    packs_db = fetch_ready_packs(only_statuses=("listed", "ready"))
    if not packs_db:
        log.info("relist.skip no_packs_in_db")
        return 0

    metros = {m.slug: m for m in load_metros()}
    verticals = {v.slug: v for v in load_verticals()}
    root = output_root()

    # Minimal PackSpec with empty rows — the publish layer will rebuild
    # metadata from the CSV's row count. For a Whop monthly-refresh push
    # only the CSV file and the title/price need to be fresh; description
    # counts are recomputed when rows are present. So we load rows if the
    # CSV exists and otherwise push whatever's listed with a placeholder.
    import csv as _csv

    specs: list[PackSpec] = []
    for row in packs_db:
        m = metros.get(row["metro_slug"])
        v = verticals.get(row["vertical_slug"])
        if not m or not v:
            continue
        seg = Segment(metro=m, vertical=v)
        csv_path = root / f"{seg.metro.slug}__{seg.vertical.slug}.csv"
        if not csv_path.exists():
            log.warning("relist.skip missing_csv seg=%s path=%s", seg.slug, csv_path)
            continue

        rows: list[Business] = []
        with csv_path.open("r", encoding="utf-8") as f:
            r = _csv.DictReader(f)
            for item in r:
                rows.append(Business(
                    name=item.get("name", ""),
                    street_address=item.get("street_address", ""),
                    tagline=item.get("tagline", ""),
                    phone=item.get("phone", ""),
                    website=item.get("website", ""),
                    owner_email=item.get("owner_email", ""),
                    category=item.get("category", ""),
                    rating=float(item["rating"]) if item.get("rating") else None,
                    review_count=int(item["review_count"]) if item.get("review_count") else None,
                    google_maps_url=item.get("google_maps_url", ""),
                    scraped_at=item.get("scraped_at", ""),
                ))
        specs.append(PackSpec(segment=seg, rows=rows, csv_path=csv_path))

    log.info("relist.start packs=%s", len(specs))
    counts = asyncio.run(list_to_channels(specs))
    log.info("relist.done counts=%s", counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
