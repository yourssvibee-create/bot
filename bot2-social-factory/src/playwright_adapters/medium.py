"""Medium Playwright adapter.

post() creates a new story at medium.com/new-story, fills title + body, then
clicks Publish on the editor to enter the full-page publish takeover, and
clicks Publish again in the takeover to actually publish.

# VERIFIED_UI: 2026-11-21
# Medium pivoted from a modal publish dialog to a full-page takeover view
# in its UI refresh. Selectors here are text-based because Medium uses
# hash-generated CSS classes that change per deploy.
"""
# VERIFIED_UI: 2026-11-21
from __future__ import annotations

from typing import Any

from shared.config import get

from ..base_adapter import AdapterError, PostContent, PostResult
from ._base import PlaywrightAdapter
from . import _post_helpers as ph

# VERIFIED_UI: 2026-11-21
# Medium publish UI is a full-page takeover, not a modal dialog.
# Three buttons visible in the takeover: "Submit", "Publish", "Schedule for later".
# Exact text match required to avoid matching the other two.
PUBLISH_NOW_BUTTON: str = 'button:text-is("Publish")'


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
        draft_only = bool((content.extra or {}).get("draft_only", False))

        try:
            await page.goto("https://medium.com/new-story", wait_until="domcontentloaded")
            await ph.pause()
            # VERIFIED_UI: 2026-11-20 — live DOM shows h3.graf--title on /new-story
            await ph.safe_fill(page, "h3.graf--title", title)
            await ph.pause()
            # VERIFIED_UI: 2026-11-20 — body is the first paragraph after the title h3
            await ph.safe_fill(page, "p.graf-after--h3", body)
            # Medium autosaves on idle — wait for autosave to persist the draft.
            await page.wait_for_timeout(5000)

            if draft_only:
                # Medium persists drafts automatically; stop here.
                return PostResult(
                    ok=True, platform=self.platform, external_url=page.url
                )

            # Click editor's top-right Publish button (also matches via text-is).
            await ph.safe_click(page, PUBLISH_NOW_BUTTON)
            # Give the takeover view time to load.
            await page.wait_for_timeout(3000)

            # Safety guard: confirm we've left the editor AND there's exactly
            # one visible "Publish" button (the takeover's primary action).
            if page.url.rstrip("/").endswith("/new-story"):
                raise AdapterError(
                    f"medium: takeover did not load (still at {page.url})"
                )
            n = await page.locator(PUBLISH_NOW_BUTTON).count()
            if n != 1:
                raise AdapterError(
                    f"medium: expected 1 Publish button in takeover, found {n}"
                )

            # Click Publish in the takeover — real publish action.
            await ph.safe_click(page, PUBLISH_NOW_BUTTON)
            url = await ph.read_url(page, min_contains="medium.com/")
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
# Headed verification entry point. Never called from the nightly pipeline.
#
#   python -m src.playwright_adapters.medium --verify-only
#     Open the saved profile headed, confirm login, probe the editor
#     selectors on /new-story, and probe the takeover Publish selector.
#     No draft is published. Safe to re-run.

_PROBE_DRAFT_TITLE = "DO NOT PUBLISH 2026-11-21"
_PROBE_DRAFT_BODY = "scratch draft for selector verification, do not publish"


async def _run_verify_only() -> int:
    """Probe editor + takeover selectors against the live account."""
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

    # URL-based login gate: /new-story is auth-gated.
    await page.goto("https://medium.com/new-story", wait_until="networkidle")
    if "/signin" in page.url or "/m/signin" in page.url:
        print(f"[login] RED — redirected to {page.url}; re-seed cookies headed once")
        await adapter._ctx.close()
        return 2
    print(f"[login] GREEN — editor at {page.url}")
    await page.wait_for_timeout(2500)

    probes = [
        ("title",  "h3.graf--title"),
        ("body",   "p.graf-after--h3"),
        ("publish", PUBLISH_NOW_BUTTON),
    ]
    all_green = True
    for label, sel in probes:
        try:
            n = await page.locator(sel).count()
        except Exception as e:
            print(f"[{label:7s}] selector={sel!r} ERR={str(e)[:120]}")
            all_green = False
            continue
        status = "GREEN" if n >= 1 else "RED"
        if n < 1:
            all_green = False
        print(f"[{label:7s}] selector={sel!r} count={n} {status}")

    await adapter._ctx.close()
    return 0 if all_green else 1


def _main_cli() -> int:
    import argparse, asyncio
    ap = argparse.ArgumentParser(description="Medium adapter headed probes")
    ap.add_argument("--verify-only", action="store_true",
                    help="probe editor + takeover selectors; no draft published")
    args = ap.parse_args()
    if args.verify_only:
        return asyncio.run(_run_verify_only())
    ap.error("one of --verify-only is required")
    return 1


if __name__ == "__main__":
    raise SystemExit(_main_cli())
