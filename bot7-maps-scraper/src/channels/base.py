"""Common ChannelLister contract. Idempotent per (segment, channel):

- create() when the channel has no product_id yet.
- update() when we already know the product_id — pushes refreshed CSV + metadata.
- delete() for cleanup / takedowns.
- refresh() is a convenience that picks create vs update based on `existing_product_id`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..listing.metadata import PackMetadata


@dataclass
class PushRequest:
    segment_slug: str
    metadata: PackMetadata
    csv_path: Path
    existing_product_id: Optional[str] = None
    # Signed URL to the hosted CSV in Supabase Storage. Adapters embed it
    # in the buyer-facing surface (Gumroad custom_receipt, Whop
    # description) so purchase delivers a real file. Blank when hosting
    # failed — adapters degrade to no-link and the digest surfaces it.
    download_url: str = ""


@dataclass
class ListResult:
    ok: bool
    product_id: str = ""
    product_url: str = ""
    error: str = ""


class ChannelLister(ABC):
    """One subclass per channel. All methods are async — concrete classes
    use httpx.AsyncClient for network work."""

    name: str = "channel"

    @abstractmethod
    async def create(self, req: PushRequest) -> ListResult: ...

    @abstractmethod
    async def update(self, req: PushRequest) -> ListResult: ...

    @abstractmethod
    async def delete(self, product_id: str) -> ListResult: ...

    async def refresh(self, req: PushRequest) -> ListResult:
        if req.existing_product_id:
            return await self.update(req)
        return await self.create(req)
