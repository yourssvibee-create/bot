"""YouTube Data API v3 adapter — uploads a video via resumable upload.

OAuth 2.0 refresh-token flow. Required secrets:
  YT_CLIENT_ID
  YT_CLIENT_SECRET
  YT_REFRESH_TOKEN      (one-shot obtained via Google's OAuth playground
                         with scope https://www.googleapis.com/auth/youtube.upload)

Uploads expect `media_urls[0]` to be a local path to a video file (.mp4,
.mov, .webm). Snippet title/description come from the caption; privacyStatus
defaults to "unlisted" and can be overridden via
`content.extra["youtube_privacy"]` ("public" | "unlisted" | "private").
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

from shared.config import get, is_placeholder
from shared.logging import get_logger

from ..base_adapter import AdapterError, BasePlatformAdapter, PostContent, PostResult
from ._api_stub import APINotRegisteredError

log = get_logger("bot2.youtube")

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_UPLOAD_URL = (
    "https://www.googleapis.com/upload/youtube/v3/videos"
    "?uploadType=resumable&part=snippet,status"
)
_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".m4v", ".mkv"}
_MAX_TITLE = 100
_MAX_DESC = 5000


class YoutubeAPIAdapter(BasePlatformAdapter):
    platform = "youtube"
    method = "api"

    def __init__(self, *, dry_run: bool = False) -> None:
        super().__init__(dry_run=dry_run)
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._client_id: str | None = None
        self._client_secret: str | None = None
        self._client: httpx.AsyncClient | None = None

    async def login(self) -> None:
        cid = get("YT_CLIENT_ID")
        cs = get("YT_CLIENT_SECRET")
        rt = get("YT_REFRESH_TOKEN")
        missing = [
            n for n, v in (
                ("YT_CLIENT_ID", cid), ("YT_CLIENT_SECRET", cs),
                ("YT_REFRESH_TOKEN", rt),
            ) if is_placeholder(v)
        ]
        if missing:
            raise APINotRegisteredError(
                f"Set {', '.join(missing)} in bot2 .env. Obtain refresh "
                "token via https://developers.google.com/oauthplayground with "
                "scope https://www.googleapis.com/auth/youtube.upload."
            )
        self._client_id = cid
        self._client_secret = cs
        self._refresh_token = rt
        self._client = httpx.AsyncClient(timeout=60)
        try:
            await self._refresh_access_token()
        except AdapterError:
            await self.close()
            raise

    async def _refresh_access_token(self) -> None:
        assert self._client is not None
        r = await self._client.post(
            _TOKEN_URL,
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": self._refresh_token,
                "grant_type": "refresh_token",
            },
        )
        if not r.is_success:
            raise AdapterError(
                f"youtube token refresh {r.status_code}: {r.text[:200]}"
            )
        self._access_token = r.json().get("access_token")
        if not self._access_token:
            raise AdapterError("youtube token refresh: no access_token in response")

    async def post(self, content: PostContent) -> PostResult:
        if self._client is None or not self._access_token:
            raise AdapterError("youtube adapter not logged in")
        if not content.media_urls:
            return PostResult(ok=False, platform=self.platform,
                              error="youtube upload requires media_urls[0] (local video file)")
        src = content.media_urls[0]
        if src.startswith(("http://", "https://")):
            return PostResult(ok=False, platform=self.platform,
                              error="youtube requires a local file; got URL")
        p = Path(src)
        if not p.is_file():
            return PostResult(ok=False, platform=self.platform,
                              error=f"youtube file not found: {src[:120]}")
        if p.suffix.lower() not in _VIDEO_EXTS:
            return PostResult(ok=False, platform=self.platform,
                              error=f"youtube: unsupported video ext {p.suffix}")

        extra: dict[str, Any] = content.extra or {}
        privacy = extra.get("youtube_privacy", "unlisted")
        if privacy not in ("public", "unlisted", "private"):
            privacy = "unlisted"

        caption = content.caption.strip()
        first_line = caption.split("\n", 1)[0] if caption else "Untitled"
        title = (extra.get("youtube_title") or first_line)[:_MAX_TITLE] or "Untitled"
        description = caption[:_MAX_DESC]

        if self.dry_run:
            log.info("youtube_dry_run",
                     extra={"path": str(p), "bytes": p.stat().st_size,
                            "title": title, "privacy": privacy})
            return PostResult(ok=True, platform=self.platform)

        snippet: dict[str, Any] = {
            "title": title,
            "description": description,
        }
        tags = extra.get("youtube_tags")
        if isinstance(tags, list) and tags:
            snippet["tags"] = [str(t)[:50] for t in tags[:15]]
        category_id = extra.get("youtube_category_id")
        if category_id:
            snippet["categoryId"] = str(category_id)

        metadata = {"snippet": snippet, "status": {"privacyStatus": privacy}}

        try:
            video_id = await self._resumable_upload(p, metadata)
        except AdapterError as e:
            return PostResult(ok=False, platform=self.platform, error=str(e)[:300])
        except Exception as e:
            return PostResult(ok=False, platform=self.platform, error=str(e)[:300])

        return PostResult(
            ok=True, platform=self.platform,
            external_url=f"https://www.youtube.com/watch?v={video_id}",
        )

    async def _resumable_upload(self, p: Path, metadata: dict[str, Any]) -> str:
        """YouTube resumable upload: init (POST metadata) -> PUT bytes."""
        assert self._client is not None and self._access_token is not None
        size = p.stat().st_size
        content_type = _video_mime(p.suffix.lower())
        init = await self._client.post(
            _UPLOAD_URL,
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": content_type,
                "X-Upload-Content-Length": str(size),
            },
            json=metadata,
        )
        if init.status_code == 401:
            # Token may have expired between login and post; refresh once.
            await self._refresh_access_token()
            init = await self._client.post(
                _UPLOAD_URL,
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type": "application/json; charset=UTF-8",
                    "X-Upload-Content-Type": content_type,
                    "X-Upload-Content-Length": str(size),
                },
                json=metadata,
            )
        if not init.is_success and init.status_code != 200:
            raise AdapterError(
                f"youtube upload init {init.status_code}: {init.text[:200]}"
            )
        location = init.headers.get("Location") or init.headers.get("location")
        if not location:
            raise AdapterError("youtube upload init: missing Location header")

        # Single PUT of the whole file — simpler than chunked PUT with
        # Content-Range, and the resumable endpoint accepts it. For files
        # up to ~2GB this is fine; chunked retry can be added later.
        put_timeout = httpx.Timeout(connect=30.0, read=600.0, write=600.0, pool=30.0)
        with p.open("rb") as fh:
            body = fh.read()
        put = await self._client.put(
            location,
            content=body,
            headers={"Content-Type": content_type},
            timeout=put_timeout,
        )
        if not put.is_success:
            raise AdapterError(
                f"youtube upload PUT {put.status_code}: {put.text[:200]}"
            )
        video_id = put.json().get("id")
        if not video_id:
            raise AdapterError(
                f"youtube upload: response missing id: {put.text[:200]}"
            )
        return video_id

    async def verify_posted(self, url: str) -> bool:
        if self._client is None or not url:
            return False
        try:
            r = await self._client.head(url, follow_redirects=True, timeout=15)
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


def _video_mime(ext: str) -> str:
    return {
        ".mp4":  "video/mp4",
        ".m4v":  "video/mp4",
        ".mov":  "video/quicktime",
        ".webm": "video/webm",
        ".mkv":  "video/x-matroska",
    }.get(ext, "application/octet-stream")
