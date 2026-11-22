"""Pinterest API v5 adapter — creates a pin from an image URL.

Requires `PINTEREST_ACCESS_TOKEN` (long-lived OAuth2 bearer) and either a
default `PINTEREST_BOARD_ID` or a per-item override in
`content.extra["pinterest_board_id"]`.

Pinterest's /v5/pins endpoint accepts image via public URL (`image_url` source
type) or base64 (`image_base64` source type). This adapter uses image_url when
`media_urls[0]` is an http(s) URL; for local paths, it reads the file and
sends base64. Video pins (source_type=video_id) require a separate upload
dance — not implemented yet; a reel with no image falls back gracefully.
"""
from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

import httpx

from shared.config import get, is_placeholder
from shared.logging import get_logger

from ..base_adapter import AdapterError, BasePlatformAdapter, PostContent, PostResult
from ._api_stub import APINotRegisteredError

log = get_logger("bot2.pinterest")

_API = "https://api.pinterest.com/v5"
_MAX_TITLE = 100
_MAX_DESC = 500


class PinterestAPIAdapter(BasePlatformAdapter):
    platform = "pinterest"
    method = "api"

    def __init__(self, *, dry_run: bool = False) -> None:
        super().__init__(dry_run=dry_run)
        self._token: str | None = None
        self._default_board: str | None = None
        self._client: httpx.AsyncClient | None = None

    async def login(self) -> None:
        token = get("PINTEREST_ACCESS_TOKEN")
        if is_placeholder(token):
            raise APINotRegisteredError(
                "Set PINTEREST_ACCESS_TOKEN in bot2 .env. Generate at "
                "https://developers.pinterest.com/apps/ -> your app -> "
                "Generate access token (needs pins:write + boards:read)."
            )
        self._token = token
        self._default_board = get("PINTEREST_BOARD_ID") or None
        self._client = httpx.AsyncClient(
            timeout=60,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        # Smoke test: fetch own user account; cheap and validates the token.
        try:
            r = await self._client.get(f"{_API}/user_account")
            if r.status_code == 401:
                raise AdapterError("pinterest 401: token invalid or expired")
            if not r.is_success:
                raise AdapterError(
                    f"pinterest /user_account {r.status_code}: {r.text[:200]}"
                )
        except AdapterError:
            await self.close()
            raise
        except Exception as e:
            await self.close()
            raise AdapterError(f"pinterest login failed: {e}") from e

    async def post(self, content: PostContent) -> PostResult:
        if self._client is None:
            raise AdapterError("pinterest adapter not logged in")
        extra: dict[str, Any] = content.extra or {}
        board_id = extra.get("pinterest_board_id") or self._default_board
        if not board_id:
            return PostResult(
                ok=False, platform=self.platform,
                error="pinterest requires PINTEREST_BOARD_ID or extra.pinterest_board_id",
            )
        if not content.media_urls:
            return PostResult(
                ok=False, platform=self.platform,
                error="pinterest pin requires media_urls[0] (image)",
            )

        media_source = _build_media_source(content.media_urls[0])
        if media_source is None:
            return PostResult(
                ok=False, platform=self.platform,
                error=f"unsupported media for pinterest: {content.media_urls[0][:120]}",
            )

        title = (extra.get("pinterest_title") or content.caption.split("\n", 1)[0])[:_MAX_TITLE]
        description = content.caption[:_MAX_DESC]
        link = extra.get("pinterest_link")

        if self.dry_run:
            log.info("pinterest_dry_run",
                     extra={"board_id": board_id, "title": title,
                            "media_type": media_source["source_type"]})
            return PostResult(ok=True, platform=self.platform)

        payload: dict[str, Any] = {
            "board_id": board_id,
            "title": title,
            "description": description,
            "media_source": media_source,
        }
        if link:
            payload["link"] = link

        try:
            r = await self._client.post(f"{_API}/pins", json=payload)
        except Exception as e:
            return PostResult(ok=False, platform=self.platform, error=str(e)[:300])
        if r.status_code == 429:
            return PostResult(
                ok=False, platform=self.platform,
                error=f"pinterest rate limited: {r.text[:200]}",
            )
        if not r.is_success:
            return PostResult(
                ok=False, platform=self.platform,
                error=f"pinterest {r.status_code}: {r.text[:200]}",
            )
        data = r.json()
        pin_id = data.get("id")
        pin_url = (
            f"https://www.pinterest.com/pin/{pin_id}/" if pin_id else None
        )
        return PostResult(ok=True, platform=self.platform, external_url=pin_url)

    async def verify_posted(self, url: str) -> bool:
        if self._client is None or not url:
            return False
        # /v5/pins/{id} needs the id; extract from /pin/{id}/
        parts = [p for p in url.rstrip("/").split("/") if p]
        if "pin" not in parts:
            return False
        pin_id = parts[parts.index("pin") + 1] if parts.index("pin") + 1 < len(parts) else None
        if not pin_id:
            return False
        try:
            r = await self._client.get(f"{_API}/pins/{pin_id}")
            return r.status_code < 400
        except Exception:
            return False

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None


def _build_media_source(url_or_path: str) -> dict[str, Any] | None:
    """Return a pinterest media_source dict for the given image url or path."""
    if url_or_path.startswith(("http://", "https://")):
        return {"source_type": "image_url", "url": url_or_path}
    p = Path(url_or_path)
    if not p.is_file():
        return None
    mime, _ = mimetypes.guess_type(str(p))
    if not mime or not mime.startswith("image/"):
        return None
    try:
        b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    except Exception:
        return None
    return {
        "source_type": "image_base64",
        "content_type": mime,
        "data": b64,
    }
