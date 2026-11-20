"""Bluesky adapter — AT Protocol via the `atproto` library.

Posts text (<=300 graphemes per bsky's rules) + up to 4 image attachments.
Credentials come from the bot folder's .env (BLUESKY_HANDLE, BLUESKY_APP_PASSWORD).
"""
from __future__ import annotations

import asyncio
import io
import mimetypes
from pathlib import Path
from typing import Any

import httpx

from shared.config import get
from shared.logging import get_logger

from ..base_adapter import AdapterError, BasePlatformAdapter, PostContent, PostResult

log = get_logger("bot2.bluesky")

MAX_IMAGES = 4
MAX_GRAPHEMES = 300  # atproto enforces this; we trim naively by chars as a safety cap
MAX_IMAGE_BYTES = 1_000_000  # bsky rejects uploads above ~976KB


class BlueskyAdapter(BasePlatformAdapter):
    platform = "bluesky"
    method = "api"

    def __init__(self, *, dry_run: bool = False) -> None:
        super().__init__(dry_run=dry_run)
        self._client: Any = None  # atproto.Client; typed as Any to keep import lazy

    async def login(self) -> None:
        handle = get("BLUESKY_HANDLE", required=True)
        app_password = get("BLUESKY_APP_PASSWORD", required=True)
        from atproto import Client  # lazy import — atproto is heavyweight

        client = Client()
        # atproto's client is sync; run its blocking login off the event loop.
        await asyncio.to_thread(client.login, handle, app_password)
        self._client = client
        log.info("bluesky_login_ok", extra={"handle": handle})

    async def post(self, content: PostContent) -> PostResult:
        if self._client is None:
            raise AdapterError("login() must be called before post()")

        text = (content.caption or "").strip()
        if len(text) > MAX_GRAPHEMES:
            text = text[: MAX_GRAPHEMES - 1].rstrip() + "…"

        images = (content.media_urls or [])[:MAX_IMAGES]

        if self.dry_run:
            log.info("bluesky_dry_run",
                     extra={"text_len": len(text), "n_images": len(images)})
            return PostResult(ok=True, platform=self.platform,
                              external_url=None, error=None)

        try:
            image_blobs = await self._fetch_images(images)
            resp = await asyncio.to_thread(
                self._send_post_sync, text, image_blobs
            )
        except Exception as e:  # atproto surfaces many exception classes
            log.warning("bluesky_post_fail", extra={"err": str(e)[:300]})
            return PostResult(ok=False, platform=self.platform, error=str(e)[:300])

        uri = getattr(resp, "uri", None) or ""
        # uri like at://did:plc:xxx/app.bsky.feed.post/xxx — build a web URL
        external = _web_url_from_uri(uri, get("BLUESKY_HANDLE", required=True))
        log.info("bluesky_post_ok", extra={"uri": uri})
        return PostResult(ok=True, platform=self.platform, external_url=external)

    async def verify_posted(self, url: str) -> bool:
        # Public resolution — no auth required.
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(url)
            return r.status_code == 200

    def _send_post_sync(self, text: str, image_blobs: list[tuple[bytes, str]]) -> Any:
        """Blocking send; called inside asyncio.to_thread."""
        if not image_blobs:
            return self._client.send_post(text=text)
        # atproto.Client.send_image / send_images
        if len(image_blobs) == 1:
            data, alt = image_blobs[0]
            return self._client.send_image(text=text, image=data, image_alt=alt)
        datas = [b for b, _ in image_blobs]
        alts = [a for _, a in image_blobs]
        return self._client.send_images(text=text, images=datas, image_alts=alts)

    async def _fetch_images(self, urls: list[str]) -> list[tuple[bytes, str]]:
        out: list[tuple[bytes, str]] = []
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
            for u in urls:
                data: bytes
                alt: str = ""
                if u.startswith("http://") or u.startswith("https://"):
                    r = await c.get(u)
                    r.raise_for_status()
                    data = r.content
                else:
                    data = Path(u).read_bytes()
                if len(data) > MAX_IMAGE_BYTES:
                    raise AdapterError(f"image {u} is {len(data)} bytes; max {MAX_IMAGE_BYTES}")
                out.append((data, alt))
        return out


def _web_url_from_uri(uri: str, handle: str) -> str:
    # at://did:plc:xxx/app.bsky.feed.post/abc123 → https://bsky.app/profile/<handle>/post/abc123
    if not uri:
        return ""
    rkey = uri.rsplit("/", 1)[-1]
    return f"https://bsky.app/profile/{handle}/post/{rkey}"
