"""Product Hunt API adapter.

Product Hunt exposes a GraphQL API (https://api.producthunt.com/v2/api/graphql)
gated by a Developer token (header: `Authorization: Bearer <token>`). Posting
a launch ("Ship") is only available to the ship's owner, so for Bot 2 we use
Product Hunt as a comment/engagement surface: post a comment on a given
product's thread when `content.extra["producthunt_slug"]` names one, otherwise
skip gracefully.

Until PRODUCTHUNT_API_TOKEN is in .env this adapter behaves as an
UnregisteredAPIAdapter — login() raises APINotRegisteredError and the router
falls back (no Playwright sibling for PH yet, so it records
skipped_not_implemented).
"""
from __future__ import annotations

from typing import Any

import httpx

from shared.config import get, is_placeholder
from shared.logging import get_logger

from ..base_adapter import AdapterError, BasePlatformAdapter, PostContent, PostResult
from ._api_stub import APINotRegisteredError

log = get_logger("bot2.producthunt")

_GRAPHQL_URL = "https://api.producthunt.com/v2/api/graphql"


class ProductHuntAdapter(BasePlatformAdapter):
    platform = "producthunt"
    method = "api"

    def __init__(self, *, dry_run: bool = False) -> None:
        super().__init__(dry_run=dry_run)
        self._token: str | None = None
        self._client: httpx.AsyncClient | None = None

    async def login(self) -> None:
        token = get("PRODUCTHUNT_API_TOKEN")
        if is_placeholder(token):
            raise APINotRegisteredError(
                "Set PRODUCTHUNT_API_TOKEN in bot2 .env. Create a Developer "
                "token at https://www.producthunt.com/v2/oauth/applications."
            )
        self._token = token
        self._client = httpx.AsyncClient(
            timeout=30,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        # Smoke test: current viewer query validates the token cheaply.
        try:
            await self._gql("query { viewer { user { id username } } }", {})
        except Exception as e:
            await self.close()
            raise AdapterError(f"producthunt viewer check failed: {e}") from e

    async def post(self, content: PostContent) -> PostResult:
        if self._client is None:
            raise AdapterError("producthunt adapter not logged in")
        extra: dict[str, Any] = content.extra or {}
        slug = extra.get("producthunt_slug")
        if not slug:
            return PostResult(
                ok=False, platform=self.platform,
                error="producthunt requires extra.producthunt_slug (target product)",
            )
        if self.dry_run:
            log.info("producthunt_dry_run",
                     extra={"slug": slug, "chars": len(content.caption)})
            return PostResult(ok=True, platform=self.platform)

        # Resolve slug -> post id, then createComment on that post.
        try:
            post_q = (
                "query($slug: String!) { post(slug: $slug) { id url } }"
            )
            post = await self._gql(post_q, {"slug": slug})
            node = ((post.get("data") or {}).get("post")) or {}
            post_id = node.get("id")
            post_url = node.get("url")
            if not post_id:
                return PostResult(
                    ok=False, platform=self.platform,
                    error=f"no producthunt post for slug={slug}",
                )
            mut = (
                "mutation($postId: ID!, $body: String!) {"
                "  commentCreate(input: {subjectId: $postId, body: $body}) {"
                "    comment { id url } errors { message field } }"
                "}"
            )
            res = await self._gql(mut, {"postId": post_id, "body": content.caption})
            payload = ((res.get("data") or {}).get("commentCreate")) or {}
            errs = payload.get("errors") or []
            if errs:
                return PostResult(
                    ok=False, platform=self.platform,
                    error=f"ph commentCreate: {errs[0].get('message', 'unknown')}",
                )
            comment = payload.get("comment") or {}
            return PostResult(
                ok=True, platform=self.platform,
                external_url=comment.get("url") or post_url,
            )
        except Exception as e:
            return PostResult(ok=False, platform=self.platform, error=str(e)[:300])

    async def verify_posted(self, url: str) -> bool:
        if self._client is None or not url:
            return False
        try:
            r = await self._client.get(url, timeout=15)
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

    async def _gql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        assert self._client is not None
        r = await self._client.post(
            _GRAPHQL_URL,
            json={"query": query, "variables": variables},
        )
        if r.status_code == 429:
            raise AdapterError(f"producthunt rate limited: {r.text[:200]}")
        if not r.is_success:
            raise AdapterError(f"producthunt {r.status_code}: {r.text[:200]}")
        body = r.json()
        if body.get("errors"):
            raise AdapterError(f"producthunt gql errors: {body['errors']}")
        return body
