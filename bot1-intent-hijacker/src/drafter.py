"""Reply drafter — Groq 70B with Dave Fieldston persona + forced disclosure."""
from __future__ import annotations

from shared.groq_client import draft as groq_draft
from shared.logging import get_logger

from .persona import DISCLOSURE_CLAUSE, SYSTEM_PROMPT, enforce_disclosure

log = get_logger("bot1.drafter")

_CHANNEL_HINTS = {
    "contact_form": (
        "Medium: a business contact form. Up to 300 words is fine. "
        "Introduce yourself, acknowledge the signal that brought you here, "
        "offer ONE concrete next step (a short call or a shared doc)."
    ),
    "reddit": (
        "Medium: a Reddit comment under a related thread. 60-120 words. "
        "Casual, no marketing speak. Prefer a specific tactic plus your "
        "affiliation disclosure."
    ),
    "quora": (
        "Medium: a Quora answer. 80-150 words. Lead with the direct answer, "
        "then disclose Ominvo, then one concrete suggestion."
    ),
    "x": (
        "Medium: a public reply on X (Twitter). <= 260 chars including any "
        "handle you quote. Disclose Ominvo in the same tweet. No thread."
    ),
}


def _user_prompt(signal: dict, channel: str) -> str:
    hint = _CHANNEL_HINTS.get(channel, _CHANNEL_HINTS["contact_form"])
    bits = [
        hint,
        "",
        "Signal to reply to:",
        f"- source: {signal.get('source')}",
        f"- url: {signal.get('url') or 'n/a'}",
    ]
    if signal.get("title"):
        bits.append(f"- title: {signal['title']}")
    if signal.get("body"):
        bits.append(f"- body: {signal['body'][:1500]}")
    bits.append("")
    bits.append(
        "Write ONE reply only. Include the first-person Ominvo disclosure. "
        'If the signal is a troll, hostile, or off-topic, output exactly: SKIP'
    )
    bits.append(f"Suggested disclosure wording (adapt as needed): {DISCLOSURE_CLAUSE}")
    return "\n".join(bits)


async def draft_reply(signal: dict, channel: str) -> str | None:
    """Returns the drafted reply or None if the model voted SKIP."""
    text = await groq_draft(SYSTEM_PROMPT, _user_prompt(signal, channel),
                            temperature=0.65, max_tokens=500)
    if text.strip().upper().startswith("SKIP") or text.strip() == "SKIP":
        log.info("draft_skipped", extra={"url": signal.get("url")})
        return None
    guarded = enforce_disclosure(text)
    return guarded
