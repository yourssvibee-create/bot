"""Flip the drafted Gumroad + Whop products to published.

Called from the approval poller after the operator taps 'Approve' on
the Telegram prompt. Idempotent — re-approving a pack is a no-op on
the channel side (both APIs accept the same publish payload against
already-published products).

Gumroad: PUT /v2/products/{id} with `published=true`. The adapter
stores the short_url at listing time, so we reuse that as the live
URL. If we don't have one, fall back to `https://gumroad.com/l/{id}`.

Whop: PATCH /api/v1/products/{id} with `visibility=visible`. The
subscriber-facing link for an access pass is
`https://whop.com/{route or id}/`. The initial create response
carries `route` when the company has a slug set; we fall back to the
raw product id.
"""
from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.logging import get_logger

from ..config import settings

log = get_logger("bot7.listing.approve")

_GUMROAD = "https://api.gumroad.com/v2"
_WHOP = "https://api.whop.com/api/v1"
_WHOP_API_VERSION = "2026-11-07"


@dataclass
class ApproveResult:
    gumroad_ok: bool
    whop_ok: bool
    gumroad_url: str
    whop_url: str
    error: str = ""


async def _publish_gumroad(product_id: str) -> tuple[bool, str, str]:
    if not product_id:
        return False, "", "no gumroad product_id"
    token = settings.gumroad_access_token
    if not token:
        return False, "", "no BOT7_GUMROAD_ACCESS_TOKEN"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.put(
                f"{_GUMROAD}/products/{product_id}",
                data={"access_token": token, "published": "true"},
            )
        if r.status_code >= 400:
            return False, "", f"gumroad {r.status_code}: {r.text[:200]}"
        body = r.json() if r.content else {}
        prod = body.get("product") or {}
        url = prod.get("short_url") or prod.get("url") or f"https://gumroad.com/l/{product_id}"
        return True, url, ""
    except httpx.HTTPError as e:
        return False, "", f"gumroad: {e}"


async def _publish_whop(product_id: str) -> tuple[bool, str, str]:
    if not product_id:
        return False, "", "no whop product_id"
    key = settings.whop_api_key
    if not key:
        return False, "", "no BOT7_WHOP_API_KEY"
    headers = {"Authorization": f"Bearer {key}", "Api-Version-Date": _WHOP_API_VERSION}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.patch(
                f"{_WHOP}/products/{product_id}",
                headers=headers,
                json={"visibility": "visible"},
            )
        if r.status_code >= 400:
            return False, "", f"whop {r.status_code}: {r.text[:200]}"
        body = r.json() if r.content else {}
        route = body.get("route") or product_id
        url = f"https://whop.com/{route}/"
        return True, url, ""
    except httpx.HTTPError as e:
        return False, "", f"whop: {e}"


def publish_pack(*, gumroad_pid: str, whop_pid: str) -> ApproveResult:
    """Flip both channels to published. One channel failing does NOT
    roll back the other — a half-published pack is better than no
    publish, and the operator sees which half succeeded in the
    Telegram update."""
    async def _run() -> ApproveResult:
        gum, whop = await asyncio.gather(
            _publish_gumroad(gumroad_pid),
            _publish_whop(whop_pid),
        )
        g_ok, g_url, g_err = gum
        w_ok, w_url, w_err = whop
        err_bits = [b for b in (g_err, w_err) if b]
        log.info(
            "approve.publish gum_ok=%s whop_ok=%s gum=%s whop=%s",
            g_ok, w_ok, g_url, w_url,
        )
        return ApproveResult(
            gumroad_ok=g_ok, whop_ok=w_ok,
            gumroad_url=g_url, whop_url=w_url,
            error=" | ".join(err_bits),
        )

    return asyncio.run(_run())
