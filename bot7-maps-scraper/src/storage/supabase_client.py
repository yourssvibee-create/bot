"""Supabase (shared Bot 1 project) writes. All tables prefixed bot7_ to
keep this unit isolated from Bot 1 / Bot 2 data."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

import psycopg

import sys
from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.logging import get_logger

from ..config import settings
from ..types import Business, Segment

log = get_logger("bot7.storage")


def _conn():
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL not set for bot7")
    return psycopg.connect(settings.database_url, autocommit=True)


def upsert_businesses(segment: Segment, businesses: list[Business]) -> int:
    """Upsert into bot7_businesses on (segment_slug, dedup_key)."""
    if not businesses:
        return 0
    if settings.dry_run:
        log.info("bot7.db.dry_run segment=%s n=%s", segment.slug, len(businesses))
        return 0

    sql = """
    insert into bot7_businesses (
        segment_slug, metro_slug, vertical_slug, dedup_key,
        name, category, street_address, tagline, phone, website, domain,
        owner_first_name, owner_last_name, owner_title, owner_email,
        email_confidence, rating, review_count, google_maps_url,
        captured_at, raw
    ) values (
        %(segment_slug)s, %(metro_slug)s, %(vertical_slug)s, %(dedup_key)s,
        %(name)s, %(category)s, %(street_address)s, %(tagline)s, %(phone)s,
        %(website)s, %(domain)s,
        %(owner_first_name)s, %(owner_last_name)s, %(owner_title)s, %(owner_email)s,
        %(email_confidence)s, %(rating)s, %(review_count)s, %(google_maps_url)s,
        now(), %(raw)s
    )
    on conflict (segment_slug, dedup_key) do update set
        name              = excluded.name,
        category          = excluded.category,
        street_address    = excluded.street_address,
        tagline           = excluded.tagline,
        phone             = excluded.phone,
        website           = excluded.website,
        domain            = excluded.domain,
        owner_first_name  = excluded.owner_first_name,
        owner_last_name   = excluded.owner_last_name,
        owner_title       = excluded.owner_title,
        owner_email       = excluded.owner_email,
        email_confidence  = excluded.email_confidence,
        rating            = excluded.rating,
        review_count      = excluded.review_count,
        google_maps_url   = excluded.google_maps_url,
        captured_at       = now(),
        raw               = excluded.raw
    """
    n = 0
    with _conn() as c, c.cursor() as cur:
        for b in businesses:
            dedup = f"{b.name.lower()}|{b.street_address[:40].lower()}"
            cur.execute(
                sql,
                {
                    "segment_slug": segment.slug,
                    "metro_slug": segment.metro.slug,
                    "vertical_slug": segment.vertical.slug,
                    "dedup_key": dedup,
                    "name": b.name,
                    "category": b.category,
                    "street_address": b.street_address,
                    "tagline": b.tagline,
                    "phone": b.phone,
                    "website": b.website,
                    "domain": b.domain,
                    "owner_first_name": b.owner_first_name,
                    "owner_last_name": b.owner_last_name,
                    "owner_title": b.owner_title,
                    "owner_email": b.owner_email,
                    "email_confidence": b.email_confidence,
                    "rating": b.rating,
                    "review_count": b.review_count,
                    "google_maps_url": b.google_maps_url,
                    "raw": json.dumps(b.to_row(), default=str),
                },
            )
            n += 1
    return n


def log_run(
    *,
    started_at: datetime,
    finished_at: datetime,
    segments_total: int,
    segments_ok: int,
    records_total: int,
    emails_found: int,
    status: str,
    notes: str = "",
) -> Optional[str]:
    if settings.dry_run:
        log.info("bot7.db.run.dry_run status=%s segments=%s/%s", status, segments_ok, segments_total)
        return None
    sql = """
    insert into bot7_runs
      (started_at, finished_at, segments_total, segments_ok,
       records_total, emails_found, status, notes)
    values (%s, %s, %s, %s, %s, %s, %s, %s)
    returning id::text
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            sql,
            (
                started_at,
                finished_at,
                segments_total,
                segments_ok,
                records_total,
                emails_found,
                status,
                notes,
            ),
        )
        row = cur.fetchone()
        return row[0] if row else None
