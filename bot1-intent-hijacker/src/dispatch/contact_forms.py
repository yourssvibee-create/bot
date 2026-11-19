"""Fill a business contact form on a target website. Heuristic field matching."""
from __future__ import annotations

from typing import Optional
from urllib.parse import urljoin, urlparse

from playwright.async_api import Page

from shared.logging import get_logger
from shared.playwright_base import launch_context

from ..config import settings
from ..persona import BIO, NAME
from .base import polite, record, screenshot_fail

log = get_logger("bot1.dispatch.contact_forms")

CONTACT_PATHS = ["/contact", "/contact-us", "/contactus", "/support", "/help", "/get-in-touch"]

SENDER_EMAIL = "dave@ominvo.com"
SENDER_NAME = NAME


def _hostname(url: str) -> str:
    try:
        return urlparse(url).netloc or url
    except Exception:
        return url


async def _find_form_page(page: Page, start_url: str) -> bool:
    try:
        await page.goto(start_url, wait_until="domcontentloaded", timeout=30_000)
    except Exception as e:
        log.warning("cf_root_fail", extra={"url": start_url, "err": str(e)})
        return False
    # Already on a form?
    if await page.locator("form").count() > 0:
        if await page.locator("textarea").count() > 0:
            return True
    # Try well-known contact paths.
    for path in CONTACT_PATHS:
        target = urljoin(start_url, path)
        try:
            await page.goto(target, wait_until="domcontentloaded", timeout=25_000)
        except Exception:
            continue
        if await page.locator("form textarea").count() > 0:
            return True
    return False


async def _fill_field(page: Page, selectors: list[str], value: str) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0:
                continue
            await loc.fill(value, timeout=5_000)
            return True
        except Exception:
            continue
    return False


async def submit_contact_form(website_url: str, message: str,
                              *, subject: str = "Question from Ominvo") -> dict:
    """Best-effort fill + submit. Returns {status, detail}."""
    cfg = settings()
    out: dict = {"status": "fail", "detail": {}}
    ctx, pw = await launch_context("bot1", "contact_forms", headless=cfg.playwright_headless)
    try:
        page = await ctx.new_page()
        found = await _find_form_page(page, website_url)
        if not found:
            out["detail"]["reason"] = "no_form_found"
            record("dispatch", target=website_url, channel="contact_form",
                   status="skip", detail=out["detail"])
            return out

        await polite()

        name_ok = await _fill_field(page, [
            "input[name*=name i]", "input[id*=name i]",
            "input[placeholder*=name i]",
        ], SENDER_NAME)

        email_ok = await _fill_field(page, [
            "input[type=email]", "input[name*=email i]", "input[id*=email i]",
        ], SENDER_EMAIL)

        subj_ok = await _fill_field(page, [
            "input[name*=subject i]", "input[id*=subject i]",
            "input[placeholder*=subject i]",
        ], subject)

        msg_ok = await _fill_field(page, [
            "textarea[name*=message i]", "textarea[name*=comment i]",
            "textarea[id*=message i]", "textarea",
        ], message)

        if not (email_ok and msg_ok):
            out["detail"] = {"reason": "required_fields_missing",
                             "name_ok": name_ok, "email_ok": email_ok,
                             "subj_ok": subj_ok, "msg_ok": msg_ok}
            shot = await screenshot_fail(page, f"cf_fields_{_hostname(website_url)}")
            if shot:
                out["detail"]["screenshot"] = shot
            record("dispatch", target=website_url, channel="contact_form",
                   status="fail", detail=out["detail"])
            return out

        if cfg.dry_run:
            out = {"status": "skip", "detail": {"reason": "dry_run"}}
            record("dispatch", target=website_url, channel="contact_form",
                   status="skip", detail=out["detail"])
            return out

        # Submit
        submit_selectors = [
            "button[type=submit]", "input[type=submit]",
            "button:has-text('Send')", "button:has-text('Submit')",
        ]
        submitted = False
        for sel in submit_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() == 0:
                    continue
                await loc.click(timeout=5_000)
                submitted = True
                break
            except Exception:
                continue
        if not submitted:
            shot = await screenshot_fail(page, f"cf_submit_{_hostname(website_url)}")
            out["detail"] = {"reason": "no_submit_button", "screenshot": shot}
            record("dispatch", target=website_url, channel="contact_form",
                   status="fail", detail=out["detail"])
            return out

        # Soft confirmation: give the page a moment to redirect or flash a thanks.
        await polite(2.0, 4.0)
        out = {"status": "ok", "detail": {"bio": BIO[:60]}}
        record("dispatch", target=website_url, channel="contact_form",
               status="ok", detail=out["detail"])
        return out
    finally:
        await ctx.close()
        await pw.stop()


async def submit(website_url: Optional[str], message: str) -> dict:
    if not website_url:
        record("dispatch", target=None, channel="contact_form",
               status="skip", detail={"reason": "no_website"})
        return {"status": "skip", "detail": {"reason": "no_website"}}
    return await submit_contact_form(website_url, message)
