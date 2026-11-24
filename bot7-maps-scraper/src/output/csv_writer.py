"""Per-segment CSV writer. One file = one sellable pack."""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from ..config import settings
from ..types import Business, Segment

_COLUMNS = [
    "name",
    "street_address",
    "tagline",
    "phone",
    "website",
    "owner_email",
    "email_confidence",
    "rating",
    "review_count",
    "category",
    "google_maps_url",
    "scraped_at",
]


def output_root(run_date: date | None = None) -> Path:
    d = (run_date or date.today()).isoformat()
    p = Path(settings.output_dir) / d
    p.mkdir(parents=True, exist_ok=True)
    return p


def write_segment_csv(
    segment: Segment, rows: list[Business], *, run_date: date | None = None
) -> Path:
    path = output_root(run_date) / f"{segment.metro.slug}__{segment.vertical.slug}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for b in rows:
            w.writerow({k: getattr(b, k) for k in _COLUMNS})
    return path
