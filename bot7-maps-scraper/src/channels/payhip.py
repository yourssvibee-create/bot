# DEFERRED — Payhip has no public product-create endpoint (coupons + license
# keys only). Revisit in Playwright-dashboard automation phase.
"""Payhip API lister.

Payhip's public API is thinner than Gumroad's — product creation is
supported on paid plans; file uploads are also plan-gated. We build the
request realistically (POST /products, PUT /products/{id}) and surface
clear error_notes when the plan doesn't allow a given op, so the
PhaseTracker shows the exact reason instead of a silent fail.

API base: https://api.payhip.com/v1
Auth header: Payhip-Api-Key: <key>
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

log = get_logger("bot7.channels.payhip")

_BASE = "https://payhip.com/api/v1"


class PayhipLister(ChannelLister):
    name = "payhip"

    def __init__(self, api_key: str | None = None) -> None:
        self.key = api_key or settings.payhip_api_key

    @property
    def configured(self) -> bool:
        return bool(self.key)

    def _headers(self) -> dict:
        return {"Payhip-Api-Key": self.key}

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
            log.info("payhip.create.dry_run seg=%s", req.segment_slug)
            return ListResult(
                ok=True,
                product_id=f"dry_payhip_{req.segment_slug}",
                product_url=f"https://dryrun.payhip.test/{req.segment_slug}",
            )
        if not self.configured:
            return ListResult(ok=False, error="payhip: no api key configured")
        payload = {
            "name": req.metadata.title,
            "description": req.metadata.description,
            "price": req.metadata.price_cents / 100,
            "currency": "USD",
            "enabled": bool(settings.listing_publish),
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await self._request(client, "POST", "/products", json=payload)
                if r.status_code >= 400:
                    return ListResult(ok=False, error=f"payhip.create {r.status_code}: {r.text[:200]}")
                body = r.json() if r.content else {}
                pid = str(body.get("id") or body.get("product_id") or "")
                url = body.get("link") or body.get("product_url") or ""
                if not pid:
                    return ListResult(ok=False, error=f"payhip.create: no id in {body}")
                upload_err = await self._upload_file(client, pid, req.csv_path)
                return ListResult(ok=True, product_id=pid, product_url=url, error=upload_err or "")
        except httpx.HTTPError as e:
            return ListResult(ok=False, error=f"payhip.create: {e}")

    async def update(self, req: PushRequest) -> ListResult:
        if settings.dry_run:
            return ListResult(
                ok=True,
                product_id=req.existing_product_id or "",
                product_url=f"https://dryrun.payhip.test/{req.segment_slug}",
            )
        if not self.configured:
            return ListResult(ok=False, error="payhip: no api key configured")
        pid = req.existing_product_id or ""
        if not pid:
            return await self.create(req)
        payload = {
            "name": req.metadata.title,
            "description": req.metadata.description,
            "price": req.metadata.price_cents / 100,
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await self._request(client, "PUT", f"/products/{pid}", json=payload)
                if r.status_code >= 400:
                    return ListResult(ok=False, product_id=pid, error=f"payhip.update {r.status_code}: {r.text[:200]}")
                upload_err = await self._upload_file(client, pid, req.csv_path)
                return ListResult(
                    ok=True, product_id=pid,
                    product_url=f"https://payhip.com/b/{pid}",
                    error=upload_err or "",
                )
        except httpx.HTTPError as e:
            return ListResult(ok=False, product_id=pid, error=f"payhip.update: {e}")

    async def delete(self, product_id: str) -> ListResult:
        if not self.configured:
            return ListResult(ok=False, error="payhip: no api key configured")
        if settings.dry_run:
            return ListResult(ok=True, product_id=product_id)
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await self._request(client, "DELETE", f"/products/{product_id}")
                if r.status_code >= 400:
                    return ListResult(ok=False, product_id=product_id, error=f"payhip.delete {r.status_code}")
            return ListResult(ok=True, product_id=product_id)
        except httpx.HTTPError as e:
            return ListResult(ok=False, product_id=product_id, error=f"payhip.delete: {e}")

    async def _upload_file(
        self, client: httpx.AsyncClient, product_id: str, csv_path: Path
    ) -> str:
        try:
            with csv_path.open("rb") as f:
                files = {"file": (csv_path.name, f, "text/csv")}
                r = await client.post(
                    f"{_BASE}/products/{product_id}/files",
                    headers=self._headers(), files=files,
                )
            if r.status_code >= 400:
                return f"payhip.file.push status={r.status_code}: {r.text[:200]}"
        except Exception as e:
            return f"payhip.file.push: {e}"
        return ""
