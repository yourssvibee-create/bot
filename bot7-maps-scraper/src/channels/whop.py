"""Whop API lister — monthly-refresh subscription tier.

Whop API: https://api.whop.com (v5 app API). A company / hub is created
in the dashboard; the API manages products ("access passes"), plans, and
the hosted file drops subscribers see. We publish:
  POST /api/v5/app/products                     -> create access pass
  POST /api/v5/app/products/{id}/plans          -> attach a monthly plan
  POST /api/v5/app/products/{id}/files          -> replace the deliverable

Bearer: BOT7_WHOP_API_KEY
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

_BASE = "https://api.whop.com/api/v5"


class WhopLister(ChannelLister):
    name = "whop"

    def __init__(self, api_key: str | None = None) -> None:
        self.key = api_key or settings.whop_api_key

    @property
    def configured(self) -> bool:
        return bool(self.key)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.key}"}

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
                # 1) access pass
                r = await self._request(
                    client, "POST", "/app/products",
                    json={
                        "name": req.metadata.title,
                        "description": req.metadata.description,
                        "visibility": "visible",
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
                # 3) deliverable
                upload_err = await self._upload_file(client, pid, req.csv_path)
                err = "; ".join(e for e in (plan_err, upload_err) if e)
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
                await self._request(
                    client, "PATCH", f"/app/products/{pid}",
                    json={
                        "name": req.metadata.title,
                        "description": req.metadata.description,
                    },
                )
                upload_err = await self._upload_file(client, pid, req.csv_path)
                return ListResult(
                    ok=True, product_id=pid,
                    product_url=f"https://whop.com/{pid}/",
                    error=upload_err or "",
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
                r = await self._request(client, "DELETE", f"/app/products/{product_id}")
                if r.status_code >= 400:
                    return ListResult(ok=False, product_id=product_id, error=f"whop.delete {r.status_code}")
            return ListResult(ok=True, product_id=product_id)
        except httpx.HTTPError as e:
            return ListResult(ok=False, product_id=product_id, error=f"whop.delete: {e}")

    async def _attach_monthly_plan(
        self, client: httpx.AsyncClient, product_id: str, price_cents: int
    ) -> str:
        try:
            r = await client.post(
                f"{_BASE}/app/products/{product_id}/plans",
                headers=self._headers(),
                json={
                    "price": price_cents,
                    "currency": "usd",
                    "billing_period": "month",
                    "trial_period_days": 0,
                },
            )
            if r.status_code >= 400:
                return f"whop.plan status={r.status_code}: {r.text[:200]}"
        except Exception as e:
            return f"whop.plan: {e}"
        return ""

    async def _upload_file(
        self, client: httpx.AsyncClient, product_id: str, csv_path: Path
    ) -> str:
        try:
            with csv_path.open("rb") as f:
                files = {"file": (csv_path.name, f, "text/csv")}
                r = await client.post(
                    f"{_BASE}/app/products/{product_id}/files",
                    headers=self._headers(), files=files,
                )
            if r.status_code >= 400:
                return f"whop.file.push status={r.status_code}: {r.text[:200]}"
        except Exception as e:
            return f"whop.file.push: {e}"
        return ""
