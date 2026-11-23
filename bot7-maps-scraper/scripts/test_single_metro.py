"""Smoke test: scrape a single metro x vertical with DRY_RUN=true.

Usage:
    poetry run python scripts/test_single_metro.py nyc restaurants
    poetry run python scripts/test_single_metro.py nyc restaurants --headful
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_BOT_ROOT = Path(__file__).resolve().parents[1]
if str(_BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(_BOT_ROOT))


def _parse_args(argv: list[str]) -> tuple[str, str, bool]:
    headful = False
    positional: list[str] = []
    for a in argv:
        if a == "--headful":
            headful = True
        else:
            positional.append(a)
    metro = positional[0] if positional else "nyc"
    vertical = positional[1] if len(positional) > 1 else "restaurants"
    return metro, vertical, headful


def main() -> int:
    metro_slug, vertical_slug, headful = _parse_args(sys.argv[1:])

    # Patch headless default BEFORE importing src.main — otherwise main.py
    # captures the original scrape_segment reference at import time and the
    # patch has no effect.
    if headful:
        import src.scrapers.google_maps as gmaps_mod
        _orig = gmaps_mod.scrape_segment

        async def _headful(segment, *, cap=120, headless=True):  # noqa: ARG001
            return await _orig(segment, cap=cap, headless=False)

        gmaps_mod.scrape_segment = _headful
        print("[smoke] headful=True — a Chromium window will open")

    from src.main import run
    from src.segments import load_metros, load_verticals
    from src.types import Segment

    # If --headful was set, make sure src.main sees the patched symbol too
    # (it already bound scrape_segment at its own import).
    if headful:
        import src.main as main_mod
        import src.scrapers.google_maps as gmaps_mod
        main_mod.scrape_segment = gmaps_mod.scrape_segment

    m = next((x for x in load_metros() if x.slug == metro_slug), None)
    v = next((x for x in load_verticals() if x.slug == vertical_slug), None)
    if not m:
        raise SystemExit(f"unknown metro: {metro_slug}")
    if not v:
        raise SystemExit(f"unknown vertical: {vertical_slug}")
    seg = Segment(metro=m, vertical=v)

    print(f"[smoke] segment={seg.slug}  query={seg.query!r}  headful={headful}")
    return asyncio.run(run([seg]))


if __name__ == "__main__":
    raise SystemExit(main())
