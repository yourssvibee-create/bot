"""Shared Playwright scaffolding for Bot 2's browser-driven adapters.

Each subclass:
  * sets `platform`, `home_url`, `login_url`, `logged_in_selector`
  * optionally overrides `_attempt_credential_login(page)` with form-fill logic

Session state persists per platform at
`bot2-social-factory/.playwright-profiles/{platform}/` via Playwright's
launch_persistent_context. First-run auth is expected to be a human doing
a headed login once; subsequent runs reuse cookies.
"""
from __future__ import annotations

import abc
from pathlib import Path
from typing import Any, Optional

from shared.logging import get_logger

from ..base_adapter import AdapterError, BasePlatformAdapter, PostContent, PostResult

log = get_logger("bot2.playwright")

_PROFILE_ROOT = Path(__file__).resolve().parents[2] / ".playwright-profiles"


class PlaywrightAdapter(BasePlatformAdapter):
    method = "playwright"

    #: landing URL after a successful login — used to detect auth state
    home_url: str = ""
    #: platform login page
    login_url: str = ""
    #: a CSS/XPath selector that only exists for signed-in users
    logged_in_selector: str = ""
    #: headless by default; subclasses or callers can flip for first-time setup
    headless: bool = True

    def __init__(self, *, dry_run: bool = False, headless: Optional[bool] = None) -> None:
        super().__init__(dry_run=dry_run)
        if headless is not None:
            self.headless = headless
        self._pw: Any = None     # playwright instance
        self._ctx: Any = None    # persistent browser context
        self._page: Any = None

    @property
    def profile_dir(self) -> Path:
        p = _PROFILE_ROOT / self.platform
        p.mkdir(parents=True, exist_ok=True)
        return p

    async def login(self) -> None:
        """Launch persistent context; if not authed, try form-fill then bail
        with a clear message so the human can finish headed, once."""
        if self.dry_run:
            log.info("playwright_login_skip_dry", extra={"platform": self.platform})
            return
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._ctx = await self._pw.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            headless=self.headless,
            viewport={"width": 1280, "height": 820},
        )
        self._page = self._ctx.pages[0] if self._ctx.pages else await self._ctx.new_page()

        await self._page.goto(self.home_url or self.login_url, wait_until="domcontentloaded")

        if await self._is_logged_in():
            log.info("playwright_session_reused", extra={"platform": self.platform})
            return

        log.info("playwright_login_attempt", extra={"platform": self.platform})
        try:
            await self._attempt_credential_login(self._page)
        except NotImplementedError:
            # Subclass has no scripted login — human must handle headed once.
            raise AdapterError(
                f"{self.platform}: no cached session at {self.profile_dir}. "
                f"Run this adapter once with headless=False and complete login "
                "manually; cookies will persist for subsequent headless runs."
            )

        if not await self._is_logged_in():
            raise AdapterError(
                f"{self.platform}: credential login did not produce an authed "
                f"session. Finish setup headed at {self.login_url}."
            )
        log.info("playwright_login_ok", extra={"platform": self.platform})

    async def _is_logged_in(self) -> bool:
        if not self.logged_in_selector or self._page is None:
            return False
        try:
            await self._page.wait_for_selector(self.logged_in_selector, timeout=3500)
            return True
        except Exception:
            return False

    @abc.abstractmethod
    async def _attempt_credential_login(self, page: Any) -> None:
        """Fill the login form with env creds. Raise NotImplementedError if
        the platform requires human-in-the-loop (captcha, SMS, etc.)."""

    async def post(self, content: PostContent) -> PostResult:
        if self.dry_run:
            log.info("playwright_post_dry", extra={"platform": self.platform,
                                                   "chars": len(content.caption)})
            return PostResult(ok=True, platform=self.platform)
        raise NotImplementedError(
            f"{self.platform}.post() is not yet implemented — "
            "revisit per-platform tomorrow (official API vs Playwright)."
        )

    async def verify_posted(self, url: str) -> bool:
        raise NotImplementedError(f"{self.platform}.verify_posted() not implemented")

    async def close(self) -> None:
        try:
            if self._ctx is not None:
                await self._ctx.close()
        finally:
            if self._pw is not None:
                await self._pw.stop()
            self._ctx = None
            self._pw = None
            self._page = None
