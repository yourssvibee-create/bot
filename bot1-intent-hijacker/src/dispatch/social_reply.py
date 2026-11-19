"""Post drafted replies to Reddit / Quora / X.

Reddit is stubbed while REDDIT_CLIENT_ID=TODO_karma_gate.
Quora + X use the Dave Fieldston persistent browser session (manual first-login).
"""
from __future__ import annotations

from playwright.async_api import Page

from shared.logging import get_logger
from shared.playwright_base import launch_context

from ..config import reddit_enabled, settings
from .base import polite, record, screenshot_fail

log = get_logger("bot1.dispatch.social")


# ----------------------------- Reddit -----------------------------

_REDDIT_SKIP_LOGGED = False


def _reddit_post(url: str, body: str) -> dict:
    global _REDDIT_SKIP_LOGGED
    if not reddit_enabled():
        if not _REDDIT_SKIP_LOGGED:
            log.info("reddit_dispatch_skipped_karma_gate")
            _REDDIT_SKIP_LOGGED = True
        record("dispatch", target=url, channel="reddit",
               status="skip", detail={"reason": "karma_gate"})
        return {"status": "skip", "detail": {"reason": "karma_gate"}}

    try:
        import praw  # noqa: WPS433
        from .. import config as cfg_mod
        reddit = praw.Reddit(
            client_id=cfg_mod.get("REDDIT_CLIENT_ID"),
            client_secret=cfg_mod.get("REDDIT_CLIENT_SECRET"),
            username=cfg_mod.get("REDDIT_USERNAME"),
            password=cfg_mod.get("REDDIT_PASSWORD"),
            user_agent=cfg_mod.get("REDDIT_USER_AGENT", "ominvo-bot1/0.1"),
        )
        sub = reddit.submission(url=url)
        if settings().dry_run:
            record("dispatch", target=url, channel="reddit",
                   status="skip", detail={"reason": "dry_run"})
            return {"status": "skip", "detail": {"reason": "dry_run"}}
        comment = sub.reply(body)
        record("dispatch", target=url, channel="reddit",
               status="ok", detail={"comment_id": comment.id})
        return {"status": "ok", "detail": {"external_id": comment.id}}
    except Exception as e:
        log.warning("reddit_post_fail", extra={"url": url, "err": str(e)})
        record("dispatch", target=url, channel="reddit",
               status="fail", detail={"err": str(e)})
        return {"status": "fail", "detail": {"err": str(e)}}


# ----------------------------- Quora ------------------------------

async def _quora_post(page: Page, url: str, body: str) -> dict:
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    except Exception as e:
        return {"status": "fail", "detail": {"err": str(e)}}
    await polite()
    try:
        await page.get_by_role("button", name="Answer").first.click(timeout=8_000)
    except Exception:
        shot = await screenshot_fail(page, "quora_no_answer_btn")
        return {"status": "fail", "detail": {"reason": "no_answer_button", "screenshot": shot}}
    await polite()
    try:
        editor = page.locator("[contenteditable=true]").first
        await editor.click()
        await editor.type(body, delay=12)
    except Exception as e:
        shot = await screenshot_fail(page, "quora_editor_fail")
        return {"status": "fail", "detail": {"err": str(e), "screenshot": shot}}

    if settings().dry_run:
        return {"status": "skip", "detail": {"reason": "dry_run"}}

    try:
        await page.get_by_role("button", name="Post").first.click(timeout=8_000)
        await polite(2, 4)
        return {"status": "ok", "detail": {}}
    except Exception as e:
        shot = await screenshot_fail(page, "quora_post_fail")
        return {"status": "fail", "detail": {"err": str(e), "screenshot": shot}}


# ------------------------------- X --------------------------------

async def _x_post(page: Page, url: str, body: str) -> dict:
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    except Exception as e:
        return {"status": "fail", "detail": {"err": str(e)}}
    await polite()
    try:
        await page.locator("[data-testid=reply]").first.click(timeout=8_000)
    except Exception:
        shot = await screenshot_fail(page, "x_no_reply_btn")
        return {"status": "fail", "detail": {"reason": "no_reply_button", "screenshot": shot}}
    await polite()
    try:
        editor = page.locator("[data-testid=tweetTextarea_0]").first
        await editor.click()
        await editor.type(body[:270], delay=12)
    except Exception as e:
        shot = await screenshot_fail(page, "x_editor_fail")
        return {"status": "fail", "detail": {"err": str(e), "screenshot": shot}}

    if settings().dry_run:
        return {"status": "skip", "detail": {"reason": "dry_run"}}

    try:
        await page.locator("[data-testid=tweetButton]").first.click(timeout=8_000)
        await polite(2, 4)
        return {"status": "ok", "detail": {}}
    except Exception as e:
        shot = await screenshot_fail(page, "x_post_fail")
        return {"status": "fail", "detail": {"err": str(e), "screenshot": shot}}


# ------------------------------ Entry -----------------------------

async def post(channel: str, url: str, body: str) -> dict:
    if channel == "reddit":
        return _reddit_post(url, body)

    if channel not in ("quora", "x"):
        record("dispatch", target=url, channel=channel,
               status="skip", detail={"reason": "unknown_channel"})
        return {"status": "skip", "detail": {"reason": "unknown_channel"}}

    cfg = settings()
    ctx, pw = await launch_context("bot1", f"dave_{channel}",
                                   headless=cfg.playwright_headless)
    try:
        page = await ctx.new_page()
        if channel == "quora":
            result = await _quora_post(page, url, body)
        else:
            result = await _x_post(page, url, body)
        record("dispatch", target=url, channel=channel,
               status=result["status"], detail=result.get("detail"))
        return result
    finally:
        await ctx.close()
        await pw.stop()
