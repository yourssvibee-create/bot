"""Score a signal 0-100 for buying intent via Groq 8B (JSON mode, Pydantic)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from shared.groq_client import score_json
from shared.logging import get_logger

log = get_logger("bot1.scoring")


class IntentScore(BaseModel):
    intent_score: int = Field(ge=0, le=100)
    reason: str
    segment: Literal["high_intent", "medium_intent", "email_only_fallback", "drop"]


SYSTEM = (
    "You are an SMB buying-intent classifier. Given a public signal (review, "
    "question, tweet, or local-business record), return STRICT JSON matching:\n"
    '{"intent_score": 0-100, "reason": "<one sentence>", '
    '"segment": "high_intent|medium_intent|email_only_fallback|drop"}\n'
    "Rules:\n"
    "- high_intent: writer explicitly says they need help NOW, is churning, or "
    "asks for alternatives. Score >= 75.\n"
    "- medium_intent: frustration or questions implying budget / shopping. 50-74.\n"
    "- email_only_fallback: a business we might cold-email even without "
    "immediate pain (e.g. low-rating local biz from maps). 35-60.\n"
    "- drop: off-topic, troll, or b2c-consumer complaint we cannot help. < 35.\n"
    "Output ONLY the JSON object, nothing else."
)


def _signal_to_user(sig: dict) -> str:
    bits = [
        f"source: {sig.get('source')}",
        f"url: {sig.get('url') or 'n/a'}",
        f"author: {sig.get('author') or 'n/a'}",
    ]
    if sig.get("title"):
        bits.append(f"title: {sig['title']}")
    if sig.get("body"):
        bits.append(f"body: {sig['body'][:1200]}")
    return "\n".join(bits)


async def score(sig: dict) -> IntentScore:
    return await score_json(SYSTEM, _signal_to_user(sig), IntentScore)
