"""X (Twitter) API v2 adapter — posts a tweet using OAuth 1.0a user context.

Requires four secrets (X Developer Portal -> your App -> Keys & tokens):
  X_API_KEY           (consumer key)
  X_API_SECRET        (consumer secret)
  X_ACCESS_TOKEN      (user access token, "Read and write" permissions)
  X_ACCESS_TOKEN_SECRET

OAuth 1.0a HMAC-SHA1 signing is hand-rolled here so we don't drag tweepy +
requests-oauthlib into the install graph. v2 /tweets accepts just
`{"text": "..."}` for text posts; media (images/video) requires a separate
upload dance against v1.1 which this adapter doesn't yet implement — a
tweet that comes in with media_urls still posts the text and logs a warning.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets as pysecrets
import time
from typing import Any
from urllib.parse import quote

import httpx

from shared.config import get, is_placeholder
from shared.logging import get_logger

from ..base_adapter import AdapterError, BasePlatformAdapter, PostContent, PostResult
from ._api_stub import APINotRegisteredError

log = get_logger("bot2.x")

_TWEET_URL = "https://api.twitter.com/2/tweets"
_ME_URL = "https://api.twitter.com/2/users/me"
_MAX_TWEET_CHARS = 280


def _oauth1_header(
    method: str, url: str, body_params: dict[str, str] | None,
    consumer_key: str, consumer_secret: str,
    access_token: str, access_secret: str,
) -> str:
    """Build an OAuth 1.0a Authorization header (HMAC-SHA1)."""
    oauth: dict[str, str] = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": pysecrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": access_token,
        "oauth_version": "1.0",
    }
    # When the request body is JSON (as it is for /2/tweets), the body does
    # NOT contribute to the signature base string per OAuth 1.0a spec; only
    # oauth_* params + URL query params do.
    all_params = {**oauth, **(body_params or {})}
    param_str = "&".join(
        f"{quote(k, safe='')}={quote(str(v), safe='')}"
        for k, v in sorted(all_params.items())
    )
    base = (
        f"{method.upper()}&{quote(url, safe='')}&{quote(param_str, safe='')}"
    )
    key = f"{quote(consumer_secret, safe='')}&{quote(access_secret, safe='')}"
    sig = base64.b64encode(
        hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()
    ).decode()
    oauth["oauth_signature"] = sig
    return "OAuth " + ", ".join(
        f'{quote(k, safe="")}="{quote(v, safe="")}"'
        for k, v in sorted(oauth.items())
    )


class XAPIAdapter(BasePlatformAdapter):
    platform = "x"
    method = "api"

    def __init__(self, *, dry_run: bool = False) -> None:
        super().__init__(dry_run=dry_run)
        self._ck: str | None = None
        self._cs: str | None = None
        self._at: str | None = None
        self._as: str | None = None
        self._client: httpx.AsyncClient | None = None

    async def login(self) -> None:
        ck = get("X_API_KEY")
        cs = get("X_API_SECRET")
        at = get("X_ACCESS_TOKEN")
        at_s = get("X_ACCESS_TOKEN_SECRET")
        missing = [
            n for n, v in (
                ("X_API_KEY", ck), ("X_API_SECRET", cs),
                ("X_ACCESS_TOKEN", at), ("X_ACCESS_TOKEN_SECRET", at_s),
            ) if is_placeholder(v)
        ]
        if missing:
            raise APINotRegisteredError(
                f"Set {', '.join(missing)} in bot2 .env (X Developer Portal, "
                "Keys & tokens, 'Read and write' permissions required)."
            )
        self._ck, self._cs, self._at, self._as = ck, cs, at, at_s  # type: ignore[assignment]
        self._client = httpx.AsyncClient(timeout=30)
        # Smoke test: GET /2/users/me validates OAuth1 signing and token.
        try:
            header = _oauth1_header("GET", _ME_URL, None,
                                    self._ck, self._cs,  # type: ignore[arg-type]
                                    self._at, self._as)  # type: ignore[arg-type]
            r = await self._client.get(_ME_URL, headers={"Authorization": header})
            if r.status_code == 401:
                raise AdapterError("x 401: OAuth1 signature rejected (check keys)")
            if not r.is_success:
                raise AdapterError(f"x /users/me {r.status_code}: {r.text[:200]}")
        except AdapterError:
            await self.close()
            raise
        except Exception as e:
            await self.close()
            raise AdapterError(f"x login failed: {e}") from e

    async def post(self, content: PostContent) -> PostResult:
        if self._client is None or self._ck is None:
            raise AdapterError("x adapter not logged in")
        text = content.caption.strip()
        if len(text) > _MAX_TWEET_CHARS:
            text = text[: _MAX_TWEET_CHARS - 1].rstrip() + "…"
        if not text:
            return PostResult(ok=False, platform=self.platform, error="empty tweet text")
        if content.media_urls:
            log.warning("x_media_ignored",
                        extra={"n_media": len(content.media_urls),
                               "reason": "media upload not implemented; posting text only"})

        if self.dry_run:
            log.info("x_dry_run", extra={"chars": len(text)})
            return PostResult(ok=True, platform=self.platform)

        body = {"text": text}
        body_bytes = json.dumps(body).encode("utf-8")
        header = _oauth1_header("POST", _TWEET_URL, None,
                                self._ck, self._cs,  # type: ignore[arg-type]
                                self._at, self._as)  # type: ignore[arg-type]
        try:
            r = await self._client.post(
                _TWEET_URL,
                content=body_bytes,
                headers={"Authorization": header,
                         "Content-Type": "application/json"},
            )
        except Exception as e:
            return PostResult(ok=False, platform=self.platform, error=str(e)[:300])
        if r.status_code == 429:
            return PostResult(ok=False, platform=self.platform,
                              error=f"x rate limited: {r.text[:200]}")
        if not r.is_success:
            return PostResult(ok=False, platform=self.platform,
                              error=f"x {r.status_code}: {r.text[:300]}")
        data = r.json()
        tweet_id = ((data.get("data") or {}).get("id"))
        if not tweet_id:
            return PostResult(ok=False, platform=self.platform,
                              error=f"x response missing id: {r.text[:200]}")
        # Build a permalink; the authoring user is on /i/web/status for a
        # stable URL without a handle, or x.com/{handle}/status for pretty.
        url = f"https://x.com/i/web/status/{tweet_id}"
        return PostResult(ok=True, platform=self.platform, external_url=url)

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
