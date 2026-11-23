"""Fast Phase-2 smoke: skip the scraper, read the most recent CSV from disk,
and drive populate_packs + list_to_channels directly (all dry_run).

Usage:
    poetry run python scripts/test_phase2.py
"""
from __future__ import annotations

import asyncio
import csv
import sys
from pathlib import Path

_BOT_ROOT = Path(__file__).resolve().parents[1]
if str(_BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(_BOT_ROOT))


def main() -> int:
    from src.listing.metadata import build_metadata, write_sample_csv
    from src.listing.publish import PackSpec, list_to_channels, populate_packs
    from src.segments import load_metros, load_verticals
    from src.types import Business, Segment

    root = Path("output")
    csvs = sorted(root.rglob("*__*.csv"))
    csvs = [p for p in csvs if "__sample" not in p.name]
    if not csvs:
        print("No CSV found under output/. Run scripts/test_single_metro.py first.")
        return 1
    csv_path = csvs[-1]
    stem = csv_path.stem           # "nyc__restaurants"
    metro_slug, _, vertical_slug = stem.partition("__")

    m = next(x for x in load_metros() if x.slug == metro_slug)
    v = next(x for x in load_verticals() if x.slug == vertical_slug)
    seg = Segment(metro=m, vertical=v)

    rows: list[Business] = []
    with csv_path.open("r", encoding="utf-8") as f:
        for item in csv.DictReader(f):
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

    print(f"[phase2] segment={seg.slug} rows={len(rows)} csv={csv_path}")
    packs = [PackSpec(segment=seg, rows=rows, csv_path=csv_path)]

    # Show one generated title + first 3 description lines so we can eyeball it.
    meta = build_metadata(seg, rows, price_usd=19, price_kind="one_time", refresh_tier="one_time")
    print(f"[phase2] title: {meta.title}")
    print("[phase2] description (preview):")
    for line in meta.description.splitlines()[:6]:
        print(f"  | {line}")

    ready = populate_packs(packs)
    print(f"[phase2] populate_packs -> ready={ready}")

    counts = asyncio.run(list_to_channels(packs))
    print(f"[phase2] list_to_channels -> {counts}")

    sample = write_sample_csv(seg, rows, Path("output") / "samples")
    print(f"[phase2] sample pack: {sample}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
