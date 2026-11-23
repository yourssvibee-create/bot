"""Auto-generate pack titles, descriptions, and redacted sample CSVs.

One pack = one metro × vertical CSV. The same metadata is pushed to
Gumroad, Payhip, and (for the monthly-refresh tier) Whop.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from ..types import Business, Segment

_MONTHS = [
    "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]

_LICENSE = (
    "License: single buyer, commercial use OK. No reselling the raw list. "
    "Data sourced from public Google Maps listings as of the date on each row."
)

_COLUMNS_BLURB = (
    "Columns: name, street_address, tagline, phone, website, owner_email, "
    "email_confidence, rating, review_count, category, google_maps_url, scraped_at."
)


@dataclass
class PackMetadata:
    title: str
    description: str
    price_cents: int
    price_kind: str           # one_time | monthly
    refresh_tier: str         # one_time | monthly


def edition_label(d: date | None = None) -> str:
    d = d or date.today()
    return f"{_MONTHS[d.month]} {d.year}"


def build_title(segment: Segment, record_count: int, *, edition: str | None = None) -> str:
    edition = edition or edition_label()
    return (
        f"{record_count} Independent {segment.vertical.name} in "
        f"{segment.metro.name} — Business Lead List ({edition} Edition)"
    )


def build_description(
    segment: Segment,
    rows: list[Business],
    *,
    price_usd: int,
    price_kind: str,
    sample_url: str | None = None,
) -> str:
    total = len(rows)
    with_email = sum(1 for r in rows if r.owner_email)
    with_website = sum(1 for r in rows if r.website)
    with_street = sum(1 for r in rows if r.street_address)
    email_pct = round(100 * with_email / total) if total else 0
    website_pct = round(100 * with_website / total) if total else 0
    street_pct = round(100 * with_street / total) if total else 0

    if price_kind == "monthly":
        cadence = (
            "Delivery: refreshed CSV re-delivered weekly to subscribers. "
            "Cancel any time."
        )
    else:
        cadence = "Delivery: instant download. One-time purchase. Future editions sold separately."

    lines = [
        f"**{total} verified independent {segment.vertical.name.lower()} in "
        f"{segment.metro.name}.**  Chains filtered out — small-business owners only.",
        "",
        f"- **{with_street}** rows with a verified street address ({street_pct}%)",
        f"- **{with_website}** rows with a website ({website_pct}%)",
        f"- **{with_email}** rows with an owner-level email contact ({email_pct}%)",
        f"- **{total}** rows with rating + review count straight from Google Maps",
        "",
        _COLUMNS_BLURB,
        "",
        cadence,
        "",
        _LICENSE,
    ]
    if sample_url:
        lines.extend(["", f"Free sample (first 10 rows, redacted): {sample_url}"])
    return "\n".join(lines)


def build_metadata(
    segment: Segment,
    rows: list[Business],
    *,
    price_usd: int,
    price_kind: str,
    refresh_tier: str,
    sample_url: str | None = None,
) -> PackMetadata:
    return PackMetadata(
        title=build_title(segment, len(rows)),
        description=build_description(
            segment, rows, price_usd=price_usd, price_kind=price_kind, sample_url=sample_url
        ),
        price_cents=price_usd * 100,
        price_kind=price_kind,
        refresh_tier=refresh_tier,
    )


_SAMPLE_COLUMNS = [
    "name", "street_address", "tagline", "phone_redacted", "website",
    "owner_email_redacted", "rating", "review_count", "category",
]


def _redact(s: str) -> str:
    if not s:
        return ""
    # Keep enough character to prove the data exists without being usable.
    if "@" in s:
        name, _, dom = s.partition("@")
        return f"{name[:1]}***@{dom}"
    digits = [c for c in s if c.isdigit()]
    if len(digits) >= 7:
        return "(***) ***-" + "".join(digits[-4:])
    return "***"


def write_sample_csv(
    segment: Segment, rows: list[Business], dest_dir: Path, *, n: int = 10
) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"{segment.metro.slug}__{segment.vertical.slug}__sample.csv"
    sample = rows[:n]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_SAMPLE_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for b in sample:
            w.writerow({
                "name": b.name,
                "street_address": b.street_address,
                "tagline": b.tagline,
                "phone_redacted": _redact(b.phone),
                "website": b.website,
                "owner_email_redacted": _redact(b.owner_email),
                "rating": b.rating,
                "review_count": b.review_count,
                "category": b.category,
            })
    return path
