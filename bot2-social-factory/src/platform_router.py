"""Reads bot2_content_queue, dispatches each item to its target platforms.

Writes one row per (content, platform) attempt to bot2_dispatches and bumps
bot2_account_health counters. API adapters run first (cheap, parallel),
Playwright adapters run sequentially (one browser context at a time).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from shared import caps as shared_caps
from shared.config import get, require_bot_id
from shared.logging import get_logger

from .base_adapter import BasePlatformAdapter, PostContent, PostResult
from . import safety

log = get_logger("bot2.router")

# How many consecutive per-platform failures flip account_health to yellow.
_YELLOW_AFTER = 2
_RED_AFTER = 5


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
    caption = content_row.get("caption", "") or ""
    payload = PostContent(
        content_type=content_row.get("content_type", "long"),
        caption=caption,
        media_urls=list(content_row.get("media_urls") or []),
        extra={"draft_only": bool(content_row.get("draft_only", False))},
    )
    results: list[DispatchRow] = []

    # --- Safety pre-flight: caption red-flag + shared caps + per-platform cap
    now_local = datetime.now()
    if safety.is_red_flag(caption):
        for p in content_row.get("target_platforms") or []:
            results.append(DispatchRow(
                content_id, p, "n/a", "red_flag",
                error="caption matched red_flag pattern pre-send",
            ))
        return results

    api_targets: list[str] = []
    pw_targets: list[str] = []
    for p in content_row.get("target_platforms") or []:
        if p not in reg:
            results.append(DispatchRow(content_id, p, "unknown", "failed",
                                       error=f"no adapter registered for {p}"))
            continue
        # Per-platform cap (safety.py, first-7-day caps + windows)
        posts_today = _bot2_posts_today(p)
        ok, reason = safety.allowed(p, posts_today, now_local)
        if not ok:
            results.append(DispatchRow(
                content_id, p, reg[p].method, "skipped_cap",
                error=f"safety.allowed denied: {reason}",
            ))
            continue
        # Cross-bot shared caps (reddit / quora / x)
        sok, sreason = shared_caps.allowed(p)
        if not sok:
            results.append(DispatchRow(
                content_id, p, reg[p].method, "skipped_shared_cap",
                error=f"shared_caps denied: {sreason}",
            ))
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

    # --- Per-result visibility log so CI output shows what happened
    # without a DB roundtrip.
    for r in results:
        log.info("dispatch_result",
                 extra={"content_id": r.content_id, "platform": r.platform,
                        "method": r.method, "status": r.status,
                        "external_url": r.external_url,
                        "error": (r.error or "")[:200]})

    # --- Persist dispatches + update account_health. DB errors are logged but
    # don't bubble up; the orchestrator's return value still reflects truth.
    _persist_dispatches(results, dry_run=dry_run)
    _update_health(results, dry_run=dry_run)
    return results


def _bot2_posts_today(platform: str) -> int:
    """Count successful Bot 2 posts to `platform` today (UTC) for safety.allowed."""
    try:
        from shared.db import fetch_one
        start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        row = fetch_one(
            "select count(*)::int as n from bot2_dispatches "
            "where platform = %s and posted_at >= %s "
            "and status in ('posted','skipped_dry_run')",
            (platform, start),
        )
        return int((row or {}).get("n") or 0)
    except Exception as e:
        log.warning("posts_today_query_fail",
                    extra={"platform": platform, "err": str(e)[:200]})
        return 0


def _persist_dispatches(results: list[DispatchRow], *, dry_run: bool) -> None:
    """Insert one row per DispatchRow into bot2_dispatches."""
    if not results:
        return
    try:
        from shared.db import execute
        for r in results:
            if not r.content_id:
                continue
            execute(
                "insert into bot2_dispatches "
                "(content_id, platform, method, posted_at, external_url, status, error) "
                "values (%s, %s, %s, now(), %s, %s, %s)",
                (r.content_id, r.platform, r.method or "unknown",
                 r.external_url, r.status, (r.error or None)),
            )
    except Exception as e:
        log.warning("dispatch_persist_fail",
                    extra={"err": str(e)[:200], "n": len(results),
                           "dry_run": dry_run})


def _update_health(results: list[DispatchRow], *, dry_run: bool) -> None:
    """Upsert bot2_account_health per platform result.

    success -> green, last_successful_post=now, consecutive_failures=0
    failure -> consecutive_failures += 1; yellow at >=_YELLOW_AFTER, red at >=_RED_AFTER
    red_flag (pattern match) is handled separately by safety.on_red_flag.
    """
    if not results:
        return
    try:
        from shared.db import execute, fetch_one
        for r in results:
            if r.status in ("posted", "skipped_dry_run"):
                execute(
                    "insert into bot2_account_health "
                    "(platform, last_successful_post, consecutive_failures, status) "
                    "values (%s, now(), 0, 'green') "
                    "on conflict (platform) do update set "
                    "last_successful_post = excluded.last_successful_post, "
                    "consecutive_failures = 0, status = 'green'",
                    (r.platform,),
                )
            elif r.status in ("failed",):
                row = fetch_one(
                    "select consecutive_failures from bot2_account_health "
                    "where platform = %s",
                    (r.platform,),
                )
                prev = int((row or {}).get("consecutive_failures") or 0)
                new_count = prev + 1
                new_status = (
                    "red" if new_count >= _RED_AFTER
                    else "yellow" if new_count >= _YELLOW_AFTER
                    else "green"
                )
                execute(
                    "insert into bot2_account_health "
                    "(platform, consecutive_failures, status, notes) "
                    "values (%s, %s, %s, %s) "
                    "on conflict (platform) do update set "
                    "consecutive_failures = excluded.consecutive_failures, "
                    "status = excluded.status, notes = excluded.notes",
                    (r.platform, new_count, new_status, (r.error or None)),
                )
            # Other statuses (skipped_*, red_flag) don't move the counter.
    except Exception as e:
        log.warning("health_update_fail",
                    extra={"err": str(e)[:200], "dry_run": dry_run})


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
            # "Missing required env var" means this platform's Playwright
            # credentials aren't set either — not a real failure, treat
            # same as the unimplemented path so the summary stays clean.
            msg = str(e)
            if "Missing required env var" in msg or "no cached session" in msg:
                return DispatchRow(content_id, platform, adapter.method,
                                   "skipped_not_implemented",
                                   error=msg[:300])
            return DispatchRow(content_id, platform, adapter.method,
                               "failed", error=msg[:300])
    except NotImplementedError:
        return DispatchRow(content_id, platform, adapter.method,
                           "skipped_not_implemented",
                           error="login not implemented")
    except Exception as e:
        # Same missing-credential logic as the API-fallback path above —
        # a Playwright-only adapter (medium, quora, nextdoor, …) that has
        # no cached session or no credentials env isn't a failure, it's an
        # un-provisioned platform.
        msg = str(e)
        if ("Missing required env var" in msg
                or "no cached session" in msg):
            return DispatchRow(content_id, platform, adapter.method,
                               "skipped_not_implemented",
                               error=msg[:300])
        return DispatchRow(content_id, platform, adapter.method,
                           "failed", error=msg[:300])

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
