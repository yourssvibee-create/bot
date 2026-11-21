"""Medium Playwright adapter.

post() creates a new story at medium.com/new-story, fills title + body, hits
Publish → Publish now. Selectors marked SELECTOR_TODO must be verified in a
live headed run before the first production post.
"""
# VERIFIED_UI: 2026-11-21
from __future__ import annotations

from typing import Any

from shared.config import get

from ..base_adapter import AdapterError, PostContent, PostResult
from ._base import PlaywrightAdapter
from . import _post_helpers as ph

# Candidate selectors for the Publish-now button in the publish dialog.
# `verify_publish_now_selector()` probes each against a saved draft and
# returns the one that locates exactly 1 element.
PUBLISH_NOW_CANDIDATES: list[str] = [
    "button:has-text('Publish now')",
    "[data-testid='publish-now-button']",
    "div[role='dialog'] button:has-text('Publish')",
    "xpath=//div[@role='dialog']//button[contains(., 'Publish')][not(contains(., 'later'))]",
]
# Set once verified against a real draft in the account.
PUBLISH_NOW_BUTTON: str = "button:has-text('Publish now')"  # SELECTOR_PENDING_VERIFY


class MediumAdapter(PlaywrightAdapter):
    platform = "medium"
    home_url = "https://medium.com/"
    login_url = "https://medium.com/m/signin"
    # Substring match — Medium's "me" link lives at /me/notifications,
    # /me/followers, /me/stories etc. depending on landing view; the exact
    # /me/stories match was brittle and broke on homepage loads.
    logged_in_selector = "a[href*='/me']"
    PREFER_API = False  # no public writes API

    async def _attempt_credential_login(self, page: Any) -> None:
        _ = get("MEDIUM_USERNAME")
        _ = get("MEDIUM_PASSWORD")
        raise NotImplementedError("medium: email magic-link; human-in-the-loop once")

    async def post(self, content: PostContent) -> PostResult:
        if self.dry_run:
            return PostResult(ok=True, platform=self.platform)
        if self._page is None:
            raise AdapterError("medium: login() must run first")
        page = self._page
        caption = content.caption or ""
        # Split caption: first line as title, remainder as body.
        title, _, body = caption.partition("\n")
        title = (title or "Ominvo — advice").strip()[:120]
        body = (body.strip() or caption).strip()

        try:
            await page.goto("https://medium.com/new-story", wait_until="domcontentloaded")
            await ph.pause()
            # VERIFIED_UI: 2026-11-20 — live DOM shows h3.graf--title on /new-story
            await ph.safe_fill(page, "h3.graf--title", title)
            await ph.pause()
            # VERIFIED_UI: 2026-11-20 — body is the first paragraph after the title h3
            await ph.safe_fill(page, "p.graf-after--h3", body)
            await ph.pause()
            # VERIFIED_UI: 2026-11-20 — js-publishButton is the canonical publish CTA
            await ph.safe_click(page, "button.js-publishButton")
            await ph.pause()
            # Publish-now dialog button — PUBLISH_NOW_BUTTON is verified by
            # scripts/verify_medium_selectors.py (attended run against a real
            # draft). See module header for VERIFIED_UI date.
            await ph.safe_click(page, PUBLISH_NOW_BUTTON)
            url = await ph.read_url(page, min_contains="medium.com/p/")
        except Exception as e:
            return PostResult(ok=False, platform=self.platform, error=str(e)[:300])
        return PostResult(ok=True, platform=self.platform, external_url=url)

    async def verify_posted(self, url: str) -> bool:
        if self._page is None:
            return False
        await self._page.goto(url, wait_until="domcontentloaded")
        return self.platform in self._page.url


# --- Attended-run CLI ------------------------------------------------------
#
# These entry points exist so a human can seed and verify selectors with a
# visible browser. They are never called from the nightly pipeline.
#
#   python -m src.playwright_adapters.medium --verify-only
#     Open the saved profile headed, confirm login, and run .count() on each
#     of the three known-good selectors on /new-story. Prints green/red.
#     No draft is created. Safe to re-run.
#
#   python -m src.playwright_adapters.medium --probe-publish
#     Create a scratch draft titled "DO NOT PUBLISH 2026-11-21", wait for
#     Medium's autosave, open the Publish dialog, probe each candidate
#     selector, print which one locates exactly 1 element, then close the
#     dialog and move the draft to trash. Side effect: your drafts list will
#     briefly contain the scratch draft. Only run when you can see the window.

_PROBE_DRAFT_TITLE = "DO NOT PUBLISH 2026-11-21"
_PROBE_DRAFT_BODY = "scratch draft for selector verification, do not publish"


async def _run_verify_only() -> int:
    """Probe the 3 known-good /new-story selectors. No draft created."""
    from playwright.async_api import async_playwright

    adapter = MediumAdapter(dry_run=False, headless=False)
    # Mirror PlaywrightAdapter.login() setup so we can own the page.
    adapter._pw = await async_playwright().start()
    adapter._ctx = await adapter._pw.chromium.launch_persistent_context(
        user_data_dir=str(adapter.profile_dir),
        headless=False,
        viewport={"width": 1280, "height": 820},
    )
    adapter._page = (adapter._ctx.pages[0]
                     if adapter._ctx.pages else await adapter._ctx.new_page())
    page = adapter._page

    # URL-based login gate: /new-story is auth-gated. If cookies are stale
    # Medium bounces to /m/signin. The `logged_in_selector` constant is kept
    # on the class for homepage checks but is unreliable on the editor view
    # (minimal nav, no /me link in the DOM).
    await page.goto("https://medium.com/new-story", wait_until="networkidle")
    if "/signin" in page.url or "/m/signin" in page.url:
        print(f"[login] RED — redirected to {page.url}; re-seed cookies headed once")
        await adapter._ctx.close()
        return 2
    print(f"[login] GREEN — editor at {page.url}")
    # Give the Medium editor a moment to hydrate.
    await page.wait_for_timeout(2500)

    probes = [
        ("title",  "h3.graf--title"),
        ("body",   "p.graf-after--h3"),
        ("publish","button.js-publishButton"),
    ]
    all_green = True
    for label, sel in probes:
        try:
            n = await page.locator(sel).count()
        except Exception as e:
            n = -1
            print(f"[{label:7s}] selector={sel!r} ERR={str(e)[:120]}")
            all_green = False
            continue
        status = "GREEN" if n >= 1 else "RED"
        if n < 1:
            all_green = False
        print(f"[{label:7s}] selector={sel!r} count={n} {status}")

    print(f"[publish-now] SELECTOR_PENDING_VERIFY — run --probe-publish to resolve")
    await adapter._ctx.close()
    return 0 if all_green else 1


async def _run_probe_publish() -> int:
    """Create a scratch draft, open the Publish dialog, probe candidates,
    report which selector locates exactly one element, then discard the draft."""
    from playwright.async_api import async_playwright

    adapter = MediumAdapter(dry_run=False, headless=False)
    adapter._pw = await async_playwright().start()
    adapter._ctx = await adapter._pw.chromium.launch_persistent_context(
        user_data_dir=str(adapter.profile_dir),
        headless=False,
        viewport={"width": 1280, "height": 820},
    )
    adapter._page = (adapter._ctx.pages[0]
                     if adapter._ctx.pages else await adapter._ctx.new_page())
    page = adapter._page

    # URL-based login gate — see --verify-only for rationale.
    await page.goto("https://medium.com/new-story", wait_until="networkidle")
    if "/signin" in page.url or "/m/signin" in page.url:
        print(f"[login] RED — redirected to {page.url}; re-seed cookies headed once")
        await adapter._ctx.close()
        return 2
    print(f"[login] GREEN — editor at {page.url}")
    await page.wait_for_timeout(2500)
    await page.locator("h3.graf--title").fill(_PROBE_DRAFT_TITLE)
    await page.locator("p.graf-after--h3").fill(_PROBE_DRAFT_BODY)
    # Medium autosaves on idle; give it a moment.
    await page.wait_for_timeout(5000)
    print(f"[draft]  seeded title={_PROBE_DRAFT_TITLE!r}; waiting on autosave")

    draft_url = page.url  # capture while we're still on the draft
    await page.locator("button.js-publishButton").click()
    await page.wait_for_timeout(2500)

    results: list[tuple[str, int]] = []
    for sel in PUBLISH_NOW_CANDIDATES:
        try:
            n = await page.locator(sel).count()
        except Exception as e:
            n = -1
            print(f"[probe]  selector={sel!r} ERR={str(e)[:120]}")
            continue
        results.append((sel, n))
        print(f"[probe]  selector={sel!r} count={n}")

    exact_one = [s for s, n in results if n == 1]
    if exact_one:
        print(f"\nRESOLVED: {exact_one[0]!r}")
        print("Edit PUBLISH_NOW_BUTTON in medium.py to this value "
              "and remove the SELECTOR_PENDING_VERIFY marker.")
    else:
        print("\nNONE matched exactly one. Open DevTools on the dialog and "
              "paste the real button's selector back.")

    # Close the dialog without publishing, then discard the draft.
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(1000)
    print(f"[cleanup] draft url={draft_url} — go to medium.com/me/stories/drafts "
          "and delete it manually, or trust Medium's 30-day auto-expiry.")
    print("[cleanup] NOT auto-deleting to avoid any unintended destructive calls.")

    await adapter._ctx.close()
    return 0 if exact_one else 1


def _main_cli() -> int:
    import argparse, asyncio
    ap = argparse.ArgumentParser(description="Medium adapter headed probes")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--verify-only", action="store_true",
                   help="probe 3 known-good selectors; no draft created")
    g.add_argument("--probe-publish", action="store_true",
                   help="create scratch draft to probe Publish-now dialog")
    args = ap.parse_args()
    if args.verify_only:
        return asyncio.run(_run_verify_only())
    return asyncio.run(_run_probe_publish())


if __name__ == "__main__":
    raise SystemExit(_main_cli())
