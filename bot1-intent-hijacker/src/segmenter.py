"""Segment leads based on score + reachable channels."""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

from shared.logging import get_logger

log = get_logger("bot1.segmenter")


def reachable_channel(signal: dict) -> Optional[str]:
    """Which social channel can we actually reply on given the signal source?"""
    src = signal.get("source")
    if src == "reddit":
        return "reddit"
    if src == "quora":
        return "quora"
    if src == "x":
        return "x"
    return None


def extract_website(signal: dict) -> Optional[str]:
    """Extract a business website from the signal when the medium implies one."""
    src = signal.get("source")
    if src == "bot7_maps":
        return signal.get("url")
    if src == "trustpilot":
        # trustpilot urls include the reviewed domain in the path
        raw = signal.get("raw") or {}
        return raw.get("business_url") or signal.get("url")
    return None


def _host(url: str) -> str:
    try:
        return urlparse(url).netloc
    except Exception:
        return url


def classify(signal: dict, intent_score: int, suggested_segment: str) -> dict:
    """Return {segment, channel, website} given the scored signal.

    The LLM's suggested segment is respected unless it conflicts with what we
    can actually reach: e.g. high_intent with no reachable channel -> keep the
    segment but set channel=contact_form if we have a website, else fall back
    to email_only_fallback.
    """
    channel = reachable_channel(signal)
    website = extract_website(signal)
    segment = suggested_segment

    if segment in ("high_intent", "medium_intent"):
        if channel is None:
            if website:
                channel = "contact_form"
            else:
                segment = "email_only_fallback"
    elif segment == "email_only_fallback":
        channel = None
    return {"segment": segment, "channel": channel, "website": website,
            "host": _host(website) if website else None}
