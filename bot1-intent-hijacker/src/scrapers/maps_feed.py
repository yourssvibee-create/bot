"""Reads from Bot 7's `bot7_leads` table in the same Supabase DB.

Empty list (no error) when Bot 7 hasn't populated it yet.
"""
from __future__ import annotations

import hashlib
from typing import Any

from shared.db import fetch_all
from shared.logging import get_logger

from ..config import settings

log = get_logger("bot1.scraper.maps_feed")


def _sid(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:24]


def scrape() -> list[dict[str, Any]]:
    cfg = settings()
    try:
        rows = fetch_all(
            """
            select id, business_name, website, phone, category, city, country,
                   rating, review_count, captured_at
            from bot7_leads
            where captured_at > now() - interval '14 days'
              and (rating is null or rating <= 3.5)
            order by captured_at desc
            limit %s
            """,
            (cfg.max_signals_per_source,),
        )
    except Exception as e:
        # Table may not exist on a brand-new DB before schema.sql is applied.
        log.info("maps_feed_empty_or_missing", extra={"err": str(e)})
        return []

    out: list[dict[str, Any]] = []
    for r in rows:
        title = f"{r['business_name']} ({r.get('city') or ''})".strip()
        body_bits = [
            f"category={r.get('category') or 'n/a'}",
            f"rating={r.get('rating') if r.get('rating') is not None else 'n/a'}",
            f"reviews={r.get('review_count') or 0}",
        ]
        out.append({
            "source": "bot7_maps",
            "source_id": _sid(f"bot7:{r['id']}"),
            "url": r.get("website"),
            "author": None,
            "title": title[:300],
            "body": " | ".join(body_bits),
            "raw": {
                "bot7_id": str(r["id"]),
                "phone": r.get("phone"),
                "category": r.get("category"),
                "city": r.get("city"),
                "country": r.get("country"),
            },
        })
    log.info("maps_feed_done", extra={"count": len(out)})
    return out
