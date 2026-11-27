"""Upload the per-segment CSV pack to Supabase Storage and mint a
long-lived signed URL. That URL is what the buyer receives — Gumroad
and Whop both lack a usable programmatic file-attach surface, so we
host the file ourselves and embed the link in the product's receipt /
description.

Bucket: `bot7-packs` (private — only signed URLs work). Created in
migration 003. Each pack lives at `<segment_slug>/<yyyymmdd>.csv` so
every nightly refresh overwrites yesterday's file at a new path and
the URL embedded in the live product rotates with it.

All HTTP calls use the service-role key (write + sign). The anon key
cannot mint signed URLs for private buckets.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.logging import get_logger

from ..config import settings

log = get_logger("bot7.storage.csv_host")

_BUCKET = "bot7-packs"
_SIGN_SECONDS = 90 * 24 * 3600  # 90 days — a nightly refresh rotates it anyway.


def _base_url() -> str:
    url = (settings.supabase_url or "").rstrip("/")
    if not url:
        raise RuntimeError("SUPABASE_URL not set — csv_host cannot upload")
    return url


def _headers() -> dict[str, str]:
    key = settings.supabase_service_role_key
    if not key:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY not set — csv_host cannot upload")
    return {"Authorization": f"Bearer {key}", "apikey": key}


def _object_path(segment_slug: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{segment_slug}/{stamp}.csv"


def upload_and_sign(segment_slug: str, csv_path: Path) -> tuple[str, str]:
    """Upload `csv_path` to Supabase Storage and return
    (object_path, signed_url). Raises on failure.

    If the bucket row doesn't exist yet (migration 003 not applied),
    returns ('', '') and logs a warning — the caller treats a blank
    URL as "fall back to description-only, no link" so the publish
    still succeeds and the operator sees the gap in the digest."""
    if settings.dry_run:
        log.info("csv_host.dry_run seg=%s", segment_slug)
        return "", f"https://dryrun.storage.test/{segment_slug}.csv"

    obj = _object_path(segment_slug)
    base = _base_url()

    # 1) Upload (x-upsert:true — nightly refresh overwrites today's file).
    try:
        with csv_path.open("rb") as f:
            data = f.read()
        r = httpx.post(
            f"{base}/storage/v1/object/{_BUCKET}/{obj}",
            headers={
                **_headers(),
                "Content-Type": "text/csv",
                "x-upsert": "true",
            },
            content=data,
            timeout=60,
        )
    except httpx.HTTPError as e:
        log.warning("csv_host.upload.fail seg=%s err=%s", segment_slug, e)
        return "", ""
    if r.status_code == 404 and "Bucket not found" in r.text:
        log.warning(
            "csv_host.bucket.missing seg=%s — run migration 003 to create bot7-packs",
            segment_slug,
        )
        return "", ""
    if r.status_code >= 400:
        log.warning(
            "csv_host.upload.http seg=%s status=%s body=%s",
            segment_slug, r.status_code, r.text[:200],
        )
        return "", ""

    # 2) Sign.
    try:
        sr = httpx.post(
            f"{base}/storage/v1/object/sign/{_BUCKET}/{obj}",
            headers={**_headers(), "Content-Type": "application/json"},
            json={"expiresIn": _SIGN_SECONDS},
            timeout=30,
        )
    except httpx.HTTPError as e:
        log.warning("csv_host.sign.fail seg=%s err=%s", segment_slug, e)
        return obj, ""
    if sr.status_code >= 400:
        log.warning(
            "csv_host.sign.http seg=%s status=%s body=%s",
            segment_slug, sr.status_code, sr.text[:200],
        )
        return obj, ""
    body = sr.json() if sr.content else {}
    # Response shape: {"signedURL": "/object/sign/..." } — join against base.
    signed_rel = body.get("signedURL") or body.get("signedUrl") or ""
    if not signed_rel:
        log.warning("csv_host.sign.no_url seg=%s body=%s", segment_slug, str(body)[:200])
        return obj, ""
    signed_url = f"{base}/storage/v1{signed_rel}" if signed_rel.startswith("/") else signed_rel
    log.info("csv_host.ok seg=%s obj=%s", segment_slug, obj)
    return obj, signed_url
