"""Registry of 'pending items' providers for the universal morning digest.

Each bot that has approval-gated work registers a provider function that
returns a list of PendingItem dicts. The digest worker calls every
registered provider, assembles one Telegram card, and uses each item's
`approve_cb` / `reject_cb` / `edit_cb` callback_data for the inline
keyboard. Those callbacks are already handled by each bot's existing
approval_handler, so the digest adds no new per-item routing.

PendingItem shape (all strings):
    bot:         "bot1" | "bot2" | "bot7"
    item_id:     unique id within that bot (e.g. a segment_slug)
    title:       short human label
    subtitle:    optional one-liner (price, counts, etc.)
    approve_cb:  telegram callback_data for approve (handled by the bot)
    reject_cb:   telegram callback_data for reject  (handled by the bot)
    edit_cb:     telegram callback_data for edit    (handled by the bot)

Providers must be side-effect-free and fast (<2s). Any exception is
caught and logged; one broken provider never blocks the others.
"""
from __future__ import annotations

from typing import Callable, TypedDict

from .logging import get_logger

log = get_logger("shared.digest_registry")


class PendingItem(TypedDict, total=False):
    bot: str
    item_id: str
    title: str
    subtitle: str
    approve_cb: str
    reject_cb: str
    edit_cb: str


_PROVIDERS: list[Callable[[], list[PendingItem]]] = []


def register_pending_provider(fn: Callable[[], list[PendingItem]]) -> None:
    _PROVIDERS.append(fn)


def collect_pending() -> list[PendingItem]:
    out: list[PendingItem] = []
    for fn in _PROVIDERS:
        try:
            items = fn() or []
        except Exception as e:
            log.warning("provider.exc fn=%s err=%s", getattr(fn, "__name__", "?"), e)
            continue
        out.extend(items)
    return out
