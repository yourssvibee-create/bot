"""Whop API lister — monthly-refresh subscription tier.

Whop retired API v2 on 2026-11-13. Products (access passes) are now
created through REST v1:
  POST /api/v1/products                         -> create product (needs
                                                   access_pass:create +
                                                   access_pass:basic:read
                                                   on the App API key)
  POST /api/v1/plans                            -> attach a monthly plan
File delivery for the Bot 7 use case is dashboard-only (experiences +
Files app) — we do NOT attempt an API upload here; the product exists,
a human attaches the CSV in the Whop dashboard when flipping publish on.

Bearer: BOT7_WHOP_API_KEY (must be an App API key, not a Bot key).
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.logging import get_logger

from ..config import settings
from .base import ChannelLister, ListResult, PushRequest

log = get_logger("bot7.channels.whop")

_BASE = "https://api.whop.com/api/v1"
_API_VERSION = "2026-11-07"
_COMPANY_ID = "biz_LEjMBcG9t5jlYX"


class WhopLister(ChannelLister):
    name = "whop"

    def __init__(self, api_key: str | None = None) -> None:
        self.key = api_key or settings.whop_api_key

    @property
    def configured(self) -> bool:
        return bool(self.key)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.key}",
            "Api-Version-Date": _API_VERSION,
        }

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1.5, min=1, max=10))
    async def _request(
        self, client: httpx.AsyncClient, method: str, path: str, **kwargs
    ) -> httpx.Response:
        r = await client.request(method, f"{_BASE}{path}", headers=self._headers(), **kwargs)
        if r.status_code >= 500:
            r.raise_for_status()
        return r

    async def create(self, req: PushRequest) -> ListResult:
        if settings.dry_run:
            log.info("whop.create.dry_run seg=%s", req.segment_slug)
            return ListResult(
                ok=True,
                product_id=f"dry_whop_{req.segment_slug}",
                product_url=f"https://dryrun.whop.test/{req.segment_slug}",
            )
        if not self.configured:
            return ListResult(ok=False, error="whop: no api key configured")
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                # 1) product — v1 shape. Only `title` is strictly required
                # per docs; `company_id` scopes it to the right business.
                # `visibility` is a post-create concern (Whop defaults new
                # products to hidden until published from the dashboard).
                r = await self._request(
                    client, "POST", "/products",
                    json={
                        "title": req.metadata.title[:80],
                        "company_id": _COMPANY_ID,
                    },
                )
                if r.status_code >= 400:
                    return ListResult(ok=False, error=f"whop.create.product {r.status_code}: {r.text[:200]}")
                body = r.json() if r.content else {}
                pid = str(body.get("id") or "")
                url = body.get("route") or body.get("url") or ""
                if not pid:
                    return ListResult(ok=False, error=f"whop.create: no id in {body}")

                # 2) monthly plan — monthly refresh tier only.
                plan_err = await self._attach_monthly_plan(client, pid, req.metadata.price_cents)
                # 3) file delivery is dashboard-only for the Whop v1 API;
                # product stays attachment-less until a human wires an
                # experience + Files app in the dashboard.
                err = plan_err
                return ListResult(ok=True, product_id=pid, product_url=url, error=err)
        except httpx.HTTPError as e:
            return ListResult(ok=False, error=f"whop.create: {e}")

    async def update(self, req: PushRequest) -> ListResult:
        if settings.dry_run:
            return ListResult(
                ok=True,
                product_id=req.existing_product_id or "",
                product_url=f"https://dryrun.whop.test/{req.segment_slug}",
            )
        if not self.configured:
            return ListResult(ok=False, error="whop: no api key configured")
        pid = req.existing_product_id or ""
        if not pid:
            return await self.create(req)
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await self._request(
                    client, "PATCH", f"/products/{pid}",
                    json={"title": req.metadata.title[:80]},
                )
                err = ""
                if r.status_code >= 400:
                    err = f"whop.update {r.status_code}: {r.text[:200]}"
                return ListResult(
                    ok=True, product_id=pid,
                    product_url=f"https://whop.com/{pid}/",
                    error=err,
                )
        except httpx.HTTPError as e:
            return ListResult(ok=False, product_id=pid, error=f"whop.update: {e}")

    async def delete(self, product_id: str) -> ListResult:
        if not self.configured:
            return ListResult(ok=False, error="whop: no api key configured")
        if settings.dry_run:
            return ListResult(ok=True, product_id=product_id)
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await self._request(client, "DELETE", f"/products/{product_id}")
                if r.status_code >= 400:
                    return ListResult(ok=False, product_id=product_id, error=f"whop.delete {r.status_code}")
            return ListResult(ok=True, product_id=product_id)
        except httpx.HTTPError as e:
            return ListResult(ok=False, product_id=product_id, error=f"whop.delete: {e}")

    async def _attach_monthly_plan(
        self, client: httpx.AsyncClient, product_id: str, price_cents: int
    ) -> str:
        # Whop v1 renewal plan. Field names follow the v1 plans surface;
        # a soft fail here doesn't block the product listing — a human
        # can add the plan from the dashboard if the create rejects.
        try:
            r = await self._request(
                client, "POST", "/plans",
                json={
                    "product_id": product_id,
                    "plan_type": "renewal",
                    "renewal_price": price_cents / 100,
                    "initial_price": price_cents / 100,
                    "billing_period": 30,
                    "currency": "usd",
                },
            )
            if r.status_code >= 400:
                return f"whop.plan status={r.status_code}: {r.text[:200]}"
        except Exception as e:
            return f"whop.plan: {e}"
        return ""
