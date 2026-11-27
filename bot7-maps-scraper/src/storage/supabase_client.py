"""Supabase (shared Bot 1 project) writes. All tables prefixed bot7_ to
keep this unit isolated from Bot 1 / Bot 2 data."""
from __future__ import annotations

import json
from datetime import datetime, timezone
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
    # Supabase pgBouncer (port 6543, transaction pooling) recycles backends
    # between statements, so psycopg3's server-side prepared-statement cache
    # collides (`prepared statement "_pg3_0" already exists`). Disabling the
    # prepare cache keeps us compatible with the pooler.
    return psycopg.connect(
        settings.database_url, autocommit=True, prepare_threshold=None
    )


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
    on conflict on constraint bot7_runs_started_status_uniq do nothing
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


# ---------- bot7_packs ----------

def upsert_pack(
    *,
    segment_slug: str,
    metro_slug: str,
    vertical_slug: str,
    tier: int,
    price_usd: int,
    price_kind: str,
    refresh_tier: str,
    record_count: int,
    title: str,
    description: str,
    sample_csv_path: str,
    status: str,
    refreshed: bool = True,
) -> Optional[str]:
    """Insert or update the pack row for this segment."""
    if settings.dry_run:
        log.info(
            "bot7.db.pack.dry_run seg=%s status=%s rows=%s",
            segment_slug, status, record_count,
        )
        return None
    sql = """
    insert into bot7_packs (
        segment_slug, metro_slug, vertical_slug, tier, price_usd,
        price_cents, price_kind, refresh_tier, metro_tier,
        record_count, title, description, sample_csv_path,
        status, last_built_at, last_refreshed_at
    ) values (
        %(segment_slug)s, %(metro_slug)s, %(vertical_slug)s, %(tier)s, %(price_usd)s,
        %(price_cents)s, %(price_kind)s, %(refresh_tier)s, %(tier)s,
        %(record_count)s, %(title)s, %(description)s, %(sample_csv_path)s,
        %(status)s, now(), %(refreshed_at)s
    )
    on conflict (segment_slug) do update set
        tier              = excluded.tier,
        metro_tier        = excluded.tier,
        price_usd         = excluded.price_usd,
        price_cents       = excluded.price_cents,
        price_kind        = excluded.price_kind,
        refresh_tier      = excluded.refresh_tier,
        record_count      = excluded.record_count,
        title             = excluded.title,
        description       = excluded.description,
        sample_csv_path   = excluded.sample_csv_path,
        status            = excluded.status,
        last_built_at     = now(),
        last_refreshed_at = coalesce(excluded.last_refreshed_at, bot7_packs.last_refreshed_at)
    returning id::text
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            sql,
            {
                "segment_slug": segment_slug,
                "metro_slug": metro_slug,
                "vertical_slug": vertical_slug,
                "tier": tier,
                "price_usd": price_usd,
                "price_cents": price_usd * 100,
                "price_kind": price_kind,
                "refresh_tier": refresh_tier,
                "record_count": record_count,
                "title": title,
                "description": description,
                "sample_csv_path": sample_csv_path,
                "status": status,
                "refreshed_at": datetime.now(timezone.utc) if refreshed else None,
            },
        )
        row = cur.fetchone()
        return row[0] if row else None


def fetch_ready_packs(
    *, segment_slugs: Optional[list[str]] = None, only_statuses: tuple[str, ...] = ("ready", "listed"),
) -> list[dict]:
    """Return packs eligible for a channel push."""
    if settings.dry_run:
        return []
    sql = """
    select segment_slug, metro_slug, vertical_slug, tier, title, description,
           price_cents, price_kind, refresh_tier, record_count,
           gumroad_product_id, payhip_product_id, whop_product_id
    from bot7_packs
    where status = any(%s)
      and (%s::text[] is null or segment_slug = any(%s))
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(sql, (list(only_statuses), segment_slugs, segment_slugs))
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def set_pack_approval_message(
    *, segment_slug: str, chat_id: str | int, message_id: int
) -> None:
    """Remember the Telegram approval message so the poller can find
    the pack when the user taps a button."""
    if settings.dry_run:
        return
    sql = """
    update bot7_packs set
        approval_message_id = %s,
        edit_prompt_chat_id = %s
    where segment_slug = %s
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(sql, (str(message_id), str(chat_id), segment_slug))


def find_pack_by_message(
    *, chat_id: str | int, message_id: int
) -> Optional[dict]:
    """Locate the pack by its approval message or its pending edit prompt."""
    sql = """
    select segment_slug, title, description, price_cents, price_kind,
           record_count, gumroad_product_id, whop_product_id,
           download_url, approved_at, published_at,
           edit_pending_field, edit_prompt_message_id, edit_prompt_chat_id
    from bot7_packs
    where (approval_message_id = %s or edit_prompt_message_id = %s)
      and (edit_prompt_chat_id = %s or edit_prompt_chat_id is null)
    order by last_built_at desc nulls last
    limit 1
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(sql, (str(message_id), str(message_id), str(chat_id)))
        row = cur.fetchone()
        if not row:
            return None
        cols = [d.name for d in cur.description]
        return dict(zip(cols, row))


def find_pack_by_segment(segment_slug: str) -> Optional[dict]:
    sql = """
    select segment_slug, title, description, price_cents, price_kind,
           record_count, gumroad_product_id, whop_product_id,
           download_url, approved_at, published_at,
           approval_message_id, edit_prompt_chat_id
    from bot7_packs
    where segment_slug = %s
    limit 1
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(sql, (segment_slug,))
        row = cur.fetchone()
        if not row:
            return None
        cols = [d.name for d in cur.description]
        return dict(zip(cols, row))


def set_pack_edit_pending(
    *, segment_slug: str, field: str,
    prompt_chat_id: str | int, prompt_message_id: int,
) -> None:
    """Record 'we asked the user to reply with a new <field>'."""
    if settings.dry_run:
        return
    sql = """
    update bot7_packs set
        edit_pending_field = %s,
        edit_prompt_chat_id = %s,
        edit_prompt_message_id = %s
    where segment_slug = %s
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(sql, (field, str(prompt_chat_id), str(prompt_message_id), segment_slug))


def clear_pack_edit_pending(segment_slug: str) -> None:
    if settings.dry_run:
        return
    sql = """
    update bot7_packs set
        edit_pending_field = null,
        edit_prompt_message_id = null
    where segment_slug = %s
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(sql, (segment_slug,))


def apply_pack_edit(
    *, segment_slug: str, field: str, new_value: str,
) -> None:
    """Apply an edited title/price/description to bot7_packs. Price
    comes in as a string that may be '$49' or '49' — strip and coerce
    to int cents for price, store as text for title/description."""
    if settings.dry_run:
        return
    if field == "title":
        sql = "update bot7_packs set title = %s where segment_slug = %s"
        with _conn() as c, c.cursor() as cur:
            cur.execute(sql, (new_value.strip()[:200], segment_slug))
    elif field == "description":
        sql = "update bot7_packs set description = %s where segment_slug = %s"
        with _conn() as c, c.cursor() as cur:
            cur.execute(sql, (new_value.strip()[:4000], segment_slug))
    elif field == "price":
        digits = "".join(ch for ch in new_value if ch.isdigit())
        if not digits:
            raise ValueError(f"no digits in price input: {new_value!r}")
        price_usd = int(digits)
        sql = """
        update bot7_packs set
            price_usd = %s,
            price_cents = %s
        where segment_slug = %s
        """
        with _conn() as c, c.cursor() as cur:
            cur.execute(sql, (price_usd, price_usd * 100, segment_slug))
    else:
        raise ValueError(f"unknown edit field: {field}")


def mark_pack_published(
    *, segment_slug: str, approved_by: str,
    gumroad_live_url: str = "", whop_live_url: str = "",
    publish_error: str = "",
) -> None:
    if settings.dry_run:
        return
    sql = """
    update bot7_packs set
        approved_at = coalesce(approved_at, now()),
        approved_by = coalesce(approved_by, %s),
        published_at = case when %s = '' then now() else published_at end,
        publish_error = nullif(%s, ''),
        status = case when %s = '' then 'listed' else status end
    where segment_slug = %s
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            sql,
            (approved_by, publish_error, publish_error, publish_error, segment_slug),
        )


def mark_pack_rejected(*, segment_slug: str, approved_by: str) -> None:
    if settings.dry_run:
        return
    sql = """
    update bot7_packs set
        approved_at = coalesce(approved_at, now()),
        approved_by = coalesce(approved_by, %s),
        status = 'rejected'
    where segment_slug = %s
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(sql, (approved_by, segment_slug))


def get_telegram_offset() -> int:
    sql = "select last_offset from bot7_telegram_offset where id = 1"
    with _conn() as c, c.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
        return int(row[0]) if row else 0


def set_telegram_offset(offset: int) -> None:
    sql = "update bot7_telegram_offset set last_offset = %s, updated_at = now() where id = 1"
    with _conn() as c, c.cursor() as cur:
        cur.execute(sql, (int(offset),))


def set_pack_download_url(*, segment_slug: str, url: str) -> None:
    """Persist the signed Supabase Storage URL on bot7_packs so the
    approval poller can resurrect it without re-uploading."""
    if settings.dry_run or not url:
        return
    sql = """
    update bot7_packs set
        download_url = %s,
        download_url_expires = now() + interval '90 days'
    where segment_slug = %s
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(sql, (url, segment_slug))


_CHANNEL_COLS = {
    "gumroad": ("gumroad_product_id", "gumroad_listed_at", "gumroad_last_pushed_at"),
    "payhip":  ("payhip_product_id",  "payhip_listed_at",  "payhip_last_pushed_at"),
    "whop":    ("whop_product_id",    "whop_listed_at",    "whop_last_pushed_at"),
}


def update_pack_channel(
    *,
    segment_slug: str,
    channel: str,
    product_id: str,
    product_url: str,
    is_first_listing: bool,
    error_note: str = "",
) -> None:
    if settings.dry_run:
        log.info(
            "bot7.db.pack.channel.dry_run seg=%s ch=%s pid=%s first=%s err=%r",
            segment_slug, channel, product_id, is_first_listing, error_note[:80],
        )
        return
    if channel not in _CHANNEL_COLS:
        raise ValueError(f"unknown channel: {channel}")
    pid_col, listed_col, pushed_col = _CHANNEL_COLS[channel]
    sql = f"""
    update bot7_packs set
        {pid_col}    = coalesce(%(pid)s, {pid_col}),
        {listed_col} = coalesce({listed_col}, case when %(first)s then now() end),
        {pushed_col} = now(),
        status       = case when %(err)s = '' then 'listed' else bot7_packs.status end,
        error_note   = case when %(err)s = '' then null else %(err)s end
    where segment_slug = %(seg)s
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(sql, {
            "pid": product_id or None,
            "first": is_first_listing,
            "err": error_note or "",
            "seg": segment_slug,
        })
