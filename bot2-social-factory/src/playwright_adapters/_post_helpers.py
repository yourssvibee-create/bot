"""Shared helpers for Playwright adapter post() methods.

Deterministic — no humanization, no mouse jitter, no per-char typing jitter.
Just a fixed 1-second courtesy pause between distinct actions so we don't
hammer platform servers.
"""
from __future__ import annotations

import asyncio
from typing import Any

PAUSE = 1.0  # seconds between distinct actions


async def pause(seconds: float = PAUSE) -> None:
    await asyncio.sleep(seconds)


async def safe_fill(page: Any, selector: str, value: str, *, timeout: int = 8000) -> None:
    """Wait for selector, fill. Raises on timeout."""
    await page.wait_for_selector(selector, timeout=timeout)
    await page.fill(selector, value)


async def safe_click(page: Any, selector: str, *, timeout: int = 8000) -> None:
    await page.wait_for_selector(selector, timeout=timeout)
    await page.click(selector)


async def read_url(page: Any, min_contains: str | None = None, timeout: int = 15000) -> str:
    """Wait for navigation to settle on a URL (optionally containing min_contains)."""
    if min_contains:
        await page.wait_for_url(lambda u: min_contains in u, timeout=timeout)
    else:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout)
    return page.url
