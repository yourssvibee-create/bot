"""Reads bot2_content_queue, dispatches each item to its target platforms.

Writes one row per (content, platform) attempt to bot2_dispatches and bumps
bot2_account_health counters. API adapters run first (cheap, parallel),
Playwright adapters run sequentially (one browser context at a time).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from shared.config import get, require_bot_id
from shared.logging import get_logger

from .base_adapter import BasePlatformAdapter, PostContent, PostResult
from . import safety

log = get_logger("bot2.router")


# Registry maps a platform slug to (adapter_class, method). Platforms whose
# post() isn't implemented yet (everything Playwright) are still included — the
# router will attempt them, catch NotImplementedError, and record it as a skip.
def _playwright_registry() -> dict[str, type[BasePlatformAdapter]]:
    from .playwright_adapters import (
        FacebookAdapter, InstagramAdapter, LinkedInAdapter, MediumAdapter,
        NextdoorAdapter, PinterestAdapter, QuoraAdapter, ThreadsAdapter,
        TikTokAdapter, XAdapter, YouTubeAdapter,
    )
    return {
        "facebook":  FacebookAdapter,
        "instagram": InstagramAdapter,
        "linkedin":  LinkedInAdapter,
        "medium":    MediumAdapter,
        "nextdoor":  NextdoorAdapter,
        "pinterest": PinterestAdapter,
        "quora":     QuoraAdapter,
        "threads":   ThreadsAdapter,
        "tiktok":    TikTokAdapter,
        "x":         XAdapter,
        "youtube":   YouTubeAdapter,
    }


def _resolve_adapter(platform: str) -> type[BasePlatformAdapter] | None:
    """PREFER_API → try API registry first; if the API class is still the
    UnregisteredAPIAdapter stub, return the Playwright fallback instead."""
    from .api_adapters import API_REGISTRY, UnregisteredAPIAdapter
    pw = _playwright_registry()
    pw_cls = pw.get(platform)
    api_cls = API_REGISTRY.get(platform)

    if api_cls is not None and not issubclass(api_cls, UnregisteredAPIAdapter):
        return api_cls
    if pw_cls is not None and getattr(pw_cls, "PREFER_API", True) is False:
        return pw_cls
    if api_cls is not None:
        return api_cls  # the stub — will raise APINotRegisteredError, caught below
    return pw_cls


def _registry() -> dict[str, type[BasePlatformAdapter]]:
    """Legacy accessor used by run_queue iteration. Each lookup now goes
    through _resolve_adapter for the PREFER_API fallback."""
    platforms = (
        "bluesky", "facebook", "instagram", "linkedin", "medium", "nextdoor",
        "pinterest", "producthunt", "quora", "threads", "tiktok", "x",
        "youtube",
    )
    out: dict[str, type[BasePlatformAdapter]] = {}
    for p in platforms:
        cls = _resolve_adapter(p)
        if cls is not None:
            out[p] = cls
    return out


@dataclass
class DispatchRow:
    content_id: str
    platform: str
    method: str
    status: str                 # posted | failed | skipped_not_implemented | skipped_dry_run
    external_url: str | None = None
    error: str | None = None


async def dispatch_item(content_row: dict[str, Any], *, dry_run: bool) -> list[DispatchRow]:
    """Dispatch one bot2_content_queue row to each of its target_platforms."""
    reg = _registry()
    content_id = str(content_row.get("id", ""))
    payload = PostContent(
        content_type=content_row.get("content_type", "long"),
        caption=content_row.get("caption", "") or "",
        media_urls=list(content_row.get("media_urls") or []),
        extra={"draft_only": bool(content_row.get("draft_only", False))},
    )
    results: list[DispatchRow] = []

    api_targets: list[str] = []
    pw_targets: list[str] = []
    for p in content_row.get("target_platforms") or []:
        if p not in reg:
            results.append(DispatchRow(content_id, p, "unknown", "failed",
                                       error=f"no adapter registered for {p}"))
            continue
        if reg[p].method == "api":
            api_targets.append(p)
        else:
            pw_targets.append(p)

    # API adapters in parallel
    api_results = await asyncio.gather(
        *[_run_one(reg[p], p, payload, dry_run, content_id) for p in api_targets],
        return_exceptions=False,
    )
    results.extend(api_results)

    # Playwright adapters sequentially
    for p in pw_targets:
        results.append(await _run_one(reg[p], p, payload, dry_run, content_id))

    return results


async def _run_one(cls: type[BasePlatformAdapter], platform: str,
                   payload: PostContent, dry_run: bool, content_id: str) -> DispatchRow:
    from .api_adapters import APINotRegisteredError

    adapter = cls(dry_run=dry_run)
    try:
        await adapter.login()
    except APINotRegisteredError:
        # Fall back to Playwright sibling if the API tokens aren't set yet.
        pw = _playwright_registry().get(platform)
        if pw is None:
            return DispatchRow(content_id, platform, adapter.method,
                               "skipped_not_implemented",
                               error="no playwright fallback for platform")
        log.info("api_unregistered_fallback_playwright", extra={"platform": platform})
        adapter = pw(dry_run=dry_run)
        try:
            await adapter.login()
        except NotImplementedError:
            return DispatchRow(content_id, platform, adapter.method,
                               "skipped_not_implemented",
                               error="playwright login not implemented")
        except Exception as e:
            return DispatchRow(content_id, platform, adapter.method,
                               "failed", error=str(e)[:300])
    except NotImplementedError:
        return DispatchRow(content_id, platform, adapter.method,
                           "skipped_not_implemented",
                           error="login not implemented")
    except Exception as e:
        return DispatchRow(content_id, platform, adapter.method,
                           "failed", error=str(e)[:300])

    try:
        res: PostResult = await adapter.post(payload)
    except NotImplementedError as e:
        return DispatchRow(content_id, platform, adapter.method,
                           "skipped_not_implemented", error=str(e)[:300])
    except Exception as e:
        return DispatchRow(content_id, platform, adapter.method,
                           "failed", error=str(e)[:300])
    finally:
        try:
            await adapter.close()
        except Exception:
            pass

    status = "posted" if res.ok else "failed"
    if dry_run and res.ok:
        status = "skipped_dry_run"
    if status == "failed" and safety.is_red_flag(res.error):
        safety.on_red_flag(platform, res.error or "")
        status = "red_flag"
    return DispatchRow(content_id, platform, adapter.method, status,
                       external_url=res.external_url, error=res.error)


async def run_queue(items: list[dict[str, Any]], *, dry_run: bool = False) -> list[DispatchRow]:
    """Route a list of queued items (as if read from bot2_content_queue)."""
    require_bot_id({"bot2"})
    out: list[DispatchRow] = []
    for row in items:
        out.extend(await dispatch_item(row, dry_run=dry_run))
    return out
