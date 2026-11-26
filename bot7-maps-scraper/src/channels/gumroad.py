"""Gumroad API v2 lister.

API ref: https://app.gumroad.com/api
- POST   /v2/products                    -> create product
- PUT    /v2/products/:id                -> update metadata (name/description/price)
- DELETE /v2/products/:id                -> delete
- POST   /v2/products/:id/files          -> replace file (not an official v2
                                            method; we use the storefront
                                            /files endpoint — see note below)

Gumroad's public v2 API supports create/read/update/delete of the product
row but file replacement is awkward. We POST a new file to the product's
`files` collection (undocumented but widely used), falling back to
leaving a note in error_note if that pathway refuses.
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

log = get_logger("bot7.channels.gumroad")

_BASE = "https://api.gumroad.com/v2"


class GumroadLister(ChannelLister):
    name = "gumroad"

    def __init__(self, access_token: str | None = None) -> None:
        self.token = access_token or settings.gumroad_access_token

    @property
    def configured(self) -> bool:
        return bool(self.token)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1.5, min=1, max=10))
    async def _post(self, client: httpx.AsyncClient, path: str, **kwargs) -> httpx.Response:
        r = await client.post(f"{_BASE}{path}", **kwargs)
        r.raise_for_status()
        return r

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1.5, min=1, max=10))
    async def _put(self, client: httpx.AsyncClient, path: str, **kwargs) -> httpx.Response:
        r = await client.put(f"{_BASE}{path}", **kwargs)
        r.raise_for_status()
        return r

    async def create(self, req: PushRequest) -> ListResult:
        if settings.dry_run:
            log.info("gumroad.create.dry_run seg=%s", req.segment_slug)
            return ListResult(
                ok=True,
                product_id=f"dry_gum_{req.segment_slug}",
                product_url=f"https://dryrun.gumroad.test/{req.segment_slug}",
            )
        if not self.configured:
            return ListResult(ok=False, error="gumroad: no access token configured")
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                payload = {
                    "access_token": self.token,
                    "name": req.metadata.title,
                    "description": req.metadata.description,
                    "price": str(req.metadata.price_cents),
                    "customizable_price": "false",
                    "published": "true" if settings.listing_publish else "false",
                }
                r = await self._post(client, "/products", data=payload)
                body = r.json()
                prod = body.get("product") or {}
                pid = prod.get("id") or ""
                url = prod.get("short_url") or prod.get("url") or ""
                if not pid:
                    return ListResult(ok=False, error=f"gumroad: no product_id in response: {body}")

                upload_err = await self._upload_file(client, pid, req.csv_path)
                return ListResult(
                    ok=True, product_id=pid, product_url=url,
                    error=upload_err or "",
                )
        except httpx.HTTPError as e:
            return ListResult(ok=False, error=f"gumroad.create: {e}")

    async def update(self, req: PushRequest) -> ListResult:
        if settings.dry_run:
            log.info("gumroad.update.dry_run seg=%s pid=%s", req.segment_slug, req.existing_product_id)
            return ListResult(
                ok=True,
                product_id=req.existing_product_id or "",
                product_url=f"https://dryrun.gumroad.test/{req.segment_slug}",
            )
        if not self.configured:
            return ListResult(ok=False, error="gumroad: no access token configured")
        pid = req.existing_product_id or ""
        if not pid:
            return await self.create(req)
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                payload = {
                    "access_token": self.token,
                    "name": req.metadata.title,
                    "description": req.metadata.description,
                    "price": str(req.metadata.price_cents),
                }
                await self._put(client, f"/products/{pid}", data=payload)
                upload_err = await self._upload_file(client, pid, req.csv_path)
                return ListResult(
                    ok=True, product_id=pid,
                    product_url=f"https://gumroad.com/l/{pid}",
                    error=upload_err or "",
                )
        except httpx.HTTPError as e:
            return ListResult(ok=False, product_id=pid, error=f"gumroad.update: {e}")

    async def delete(self, product_id: str) -> ListResult:
        if not self.configured:
            return ListResult(ok=False, error="gumroad: no access token configured")
        if settings.dry_run:
            return ListResult(ok=True, product_id=product_id)
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.delete(
                    f"{_BASE}/products/{product_id}",
                    params={"access_token": self.token},
                )
                r.raise_for_status()
            return ListResult(ok=True, product_id=product_id)
        except httpx.HTTPError as e:
            return ListResult(ok=False, product_id=product_id, error=f"gumroad.delete: {e}")

    async def _upload_file(
        self, client: httpx.AsyncClient, product_id: str, csv_path: Path
    ) -> str:
        """Gumroad v2 file upload is a 4-step S3 presigned-multipart flow:
          1. POST /v2/files/presign      -> upload_id, key, file_url, parts[]
          2. PUT each part's presigned S3 URL with the chunk bytes
          3. POST /v2/files/complete     -> finalize the multipart upload
          4. PUT  /v2/products/{id}      -> attach via files[][url]=<file_url>

        Returns "" on success or a short diagnostic. CSVs are small, so
        the presign usually returns a single part; we still handle N
        parts defensively.
        """
        try:
            size = csv_path.stat().st_size
            # 1) presign
            r = await client.post(
                f"{_BASE}/files/presign",
                data={
                    "access_token": self.token,
                    "filename": csv_path.name,
                    "file_size": str(size),
                },
            )
            if r.status_code >= 400:
                return f"gumroad.presign status={r.status_code}: {r.text[:200]}"
            j = r.json()
            upload_id = j.get("upload_id")
            key = j.get("key")
            file_url = j.get("file_url")
            parts = j.get("parts") or []
            if not (upload_id and key and file_url and parts):
                return f"gumroad.presign: unexpected body {str(j)[:200]}"

            # 2) upload each part. Part N's chunk size = size // len(parts)
            # for the first N-1 parts, remainder on the last.
            n = len(parts)
            per = (size // n) if n > 0 else size
            completed: list[dict] = []
            with csv_path.open("rb") as f:
                for i, p in enumerate(parts):
                    part_number = p.get("part_number") or (i + 1)
                    url_ = p.get("url") or ""
                    if not url_:
                        return f"gumroad.part {part_number}: no url in presign"
                    chunk = f.read(per if i < n - 1 else size - per * (n - 1))
                    pr = await client.put(url_, content=chunk)
                    if pr.status_code >= 400:
                        return f"gumroad.part {part_number} status={pr.status_code}: {pr.text[:200]}"
                    etag = pr.headers.get("ETag", "").strip('"')
                    completed.append({"part_number": part_number, "etag": etag})

            # 3) complete
            cr = await client.post(
                f"{_BASE}/files/complete",
                json={
                    "access_token": self.token,
                    "upload_id": upload_id,
                    "key": key,
                    "parts": completed,
                },
            )
            if cr.status_code >= 400:
                return f"gumroad.complete status={cr.status_code}: {cr.text[:200]}"

            # 4) attach the finalized file_url to the product
            ar = await client.put(
                f"{_BASE}/products/{product_id}",
                data={
                    "access_token": self.token,
                    "files[][url]": file_url,
                },
            )
            if ar.status_code >= 400:
                return f"gumroad.attach status={ar.status_code}: {ar.text[:200]}"
        except Exception as e:
            return f"gumroad.file.push: {e}"
        return ""
