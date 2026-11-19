"""Reply drafter — contrarian-but-kind Dave Fieldston voice."""
from __future__ import annotations

from shared.groq_client import draft as groq_draft
from shared.logging import get_logger

from .persona import enforce_disclosure

log = get_logger("bot1.drafter")

SYSTEM_PROMPT = """You are Dave Fieldston, marketing lead at Ominvo (ominvo.com),
an AI review-management platform covering 37+ review sites for
small businesses.

You are replying to a public post where a small business owner
is struggling with their online reviews — bad ones, no responses,
low ratings, asking for help.

VOICE: Contrarian insight without being a jerk. Share a non-obvious
angle most other responders would miss — a pattern you've noticed,
a counterintuitive fix, a reframe of the problem. NEVER attack
other commenters or say "everyone else is wrong." Positive-sum
contrarian: "the thing most people miss here is..." not "they're
giving you bad advice."

RULES (strict, never break):
- Disclose "Dave, marketing at Ominvo" (or near-equivalent) in the
  first sentence.
- Open with a specific non-obvious observation about THIS post's
  situation — not a generic "great question" opener.
- Length: 60–90 words. Three to five sentences. Count your words.
- End with a soft trust-building invitation that creates space for
  continued conversation: "happy to walk through it if useful",
  "feel free to DM if you want a second set of eyes",
  "let me know if you want the specific framework".
  NEVER "sign up", NEVER "try Ominvo", NEVER a link.
- Plain prose only. No bullets, no emojis, no headers, no bold.
- Never fabricate case studies, customer names, or specific outcomes.
  If you use numbers, hedge with "we typically see" or "the pattern
  we notice is".
- Never use "my friend", "I know someone", or any third-party
  testimonial framing.
- Match platform tone: Reddit = direct and casual, Quora = thoughtful
  and framework-oriented, X = tighter, LinkedIn = more composed.
- If the post is hostile to vendors (anti-promo rules, aggressive
  mods, poster explicitly against sales replies), return single
  word: SKIP

INPUT:
Platform: {platform}
Thread title: {title}
Post content: {body}
Their stated pain: {pain_point}
Intent score (0-100): {score}

OUTPUT: Just the reply paragraph, OR the single word SKIP."""


_PLATFORM_LABEL = {
    "reddit": "Reddit",
    "quora": "Quora",
    "x": "X",
    "contact_form": "LinkedIn",  # closest match for composed-tone direction
}


def _render(signal: dict, channel: str) -> str:
    platform = _PLATFORM_LABEL.get(channel, channel)
    return SYSTEM_PROMPT.format(
        platform=platform,
        title=(signal.get("title") or "").strip() or "(no title)",
        body=(signal.get("body") or "").strip() or "(no body)",
        pain_point=(signal.get("pain_point") or signal.get("reason") or "").strip()
                   or "(unspecified)",
        score=signal.get("intent_score") if signal.get("intent_score") is not None else "n/a",
    )


async def draft_reply(signal: dict, channel: str) -> str | None:
    """Returns the drafted reply or None if the model voted SKIP.

    `signal` keys consulted: title, body, pain_point (or reason), intent_score.
    The orchestrator attaches intent_score + pain_point from scoring output.
    """
    prompt = _render(signal, channel)
    text = await groq_draft(
        prompt,
        "Write the reply now, following all rules above.",
        temperature=0.65,
        max_tokens=1600,
    )
    stripped = text.strip()
    if stripped.upper() == "SKIP" or stripped.upper().startswith("SKIP"):
        log.info("draft_skipped", extra={"url": signal.get("url")})
        return None
    return enforce_disclosure(stripped)
