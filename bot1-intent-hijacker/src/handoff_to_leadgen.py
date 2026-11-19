"""Write email-only leads to `handoff_to_leadgen` for ominvo-leadgen to pick up."""
from __future__ import annotations

from typing import Optional

from shared.db import execute
from shared.logging import get_logger

log = get_logger("bot1.handoff")


def _blurb(signal: dict, intent_reason: str) -> str:
    bits = []
    if signal.get("title"):
        bits.append(signal["title"][:200])
    if signal.get("body"):
        bits.append(signal["body"][:300])
    bits.append(f"[intent: {intent_reason}]")
    return " | ".join(bits)[:800]


def queue_for_leadgen(lead_id: str, website: str, *,
                      signal: dict, intent_reason: str,
                      contact_email: Optional[str] = None) -> None:
    execute(
        """
        insert into handoff_to_leadgen (lead_id, website, contact_email, context_blurb)
        values (%s, %s, %s, %s)
        on conflict (lead_id) do nothing
        """,
        (lead_id, website, contact_email, _blurb(signal, intent_reason)),
    )
    log.info("handoff_queued", extra={"lead_id": str(lead_id), "website": website})
