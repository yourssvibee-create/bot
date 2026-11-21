"""Daily content generation for Bot 2.

Produces the day's queue: 2 reel captions + 1 carousel outline + 1 long-form
post + 3 tweets. Rotates through `prompts/pain_points.json`. Uses
`shared.llm.draft` (Groq → Gemini → NVIDIA → OpenRouter fallback).

In --dry-run, LLM calls are skipped and deterministic stub content is emitted
instead so that `nightly_run --dry-run` works offline.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shared.config import require_bot_id
from shared.logging import get_logger

log = get_logger("bot2.content")

_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"

# Platforms whose primary body is a long advice-post (anchor: advice_post.txt).
LONG_FORM_PLATFORMS: set[str] = {
    "reddit", "quora", "linkedin", "medium", "nextdoor",
}
# Platforms whose primary body is a short hook (anchor: short_form_hooks.txt).
SHORT_FORM_PLATFORMS: set[str] = {
    "x", "bluesky", "threads",
    "instagram", "tiktok", "pinterest", "youtube",
}


# ---- per-content-type format blocks substituted into the prompt template -----

_FORMAT_BLOCKS: dict[str, str] = {
    "long_reddit_quora_nextdoor_medium": (
        "Full advice-post structure as defined above, plain prose, ~180-280 words.\n"
        "Reference other subreddits as r/<name> where relevant."
    ),
    "long_linkedin": (
        "Full advice-post structure as defined above, plain prose, ~180-280 words.\n"
        "Never write 'r/<name>'. Substitute 'on Reddit' instead."
    ),
    "tweet": (
        "4-tweet thread. Tweet 1: hook (<=240 chars). Tweet 2: rule 1 with WHY.\n"
        "Tweet 3: rules 2 and 3 compressed together. Tweet 4: Ominvo disclosed close.\n"
        "Separate tweets with a line '---' between them. No tweet numbering."
    ),
    "bluesky_threads": (
        "2-post thread. Post 1: hook + rule 1 (<=300 graphemes).\n"
        "Post 2: rules 2 and 3 + Ominvo disclosed close (<=300 graphemes).\n"
        "Separate the two posts with '---' on its own line."
    ),
    "carousel": (
        "5 slides. Each slide is a single line prefixed 'SLIDE N: '.\n"
        "Slide 1: hook. Slides 2-4: one rule per slide (rule + one-line WHY).\n"
        "Slide 5: Ominvo disclosed close."
    ),
    "reel_short": (
        "15-20 second spoken script. Three beats: hook, 3 rules fast, Ominvo close.\n"
        "Format as plain prose, no slide/scene markers. One paragraph."
    ),
    "youtube_short": (
        "30-45 second spoken script. Beat intro (3-4s), then hook, 3 rules fast,\n"
        "then Ominvo close. Prefix beats with [intro], [hook], [rule 1], etc."
    ),
    "pinterest": (
        "Single pin. 1-line hook, then 3 rules as short text-overlay lines\n"
        "(each <=60 chars), then 'ominvo.com' as the bottom line. No prose paragraphs."
    ),
}


# Which platforms each content_type targets by default (router consults this).
TARGET_PLATFORMS: dict[str, list[str]] = {
    "reel":             ["instagram", "facebook", "tiktok"],
    "carousel":         ["instagram", "facebook", "linkedin"],
    "long":             ["linkedin", "medium", "quora", "nextdoor", "facebook"],
    "tweet":            ["x", "bluesky", "threads"],
    "long_form_video":  ["youtube", "tiktok"],
}


@dataclass
class ContentItem:
    content_type: str
    caption: str
    media_urls: list[str] = field(default_factory=list)
    target_platforms: list[str] = field(default_factory=list)
    pain_point_id: str = ""
    # Which LLM served this caption — surfaced in the Telegram digest and
    # useful for tracing "why does this post sound different today."
    text_provider: str = "stub"
    # Which image provider(s) served the media — populated by the pipeline
    # after images render.
    image_providers: list[str] = field(default_factory=list)

    def as_row(self) -> dict[str, Any]:
        """Row shape matching bot2_content_queue."""
        return {
            "content_type": self.content_type,
            "caption": self.caption,
            "media_urls": self.media_urls,
            "target_platforms": self.target_platforms,
            "status": "queued",
        }


def load_pain_points() -> list[dict[str, Any]]:
    return json.loads((_PROMPTS_DIR / "pain_points.json").read_text(encoding="utf-8"))


def _load_prompt_template() -> str:
    return (_PROMPTS_DIR / "advice_post.txt").read_text(encoding="utf-8")


def load_short_hooks() -> list[str]:
    """Return hooks from short_form_hooks.txt, skipping blank/comment lines."""
    raw = (_PROMPTS_DIR / "short_form_hooks.txt").read_text(encoding="utf-8")
    out = []
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


def _format_for_content_type(content_type: str, platform_hint: str) -> tuple[str, str]:
    """Return (format_name, format_block) for the given content_type."""
    if content_type == "tweet":
        return "tweet", _FORMAT_BLOCKS["tweet"]
    if content_type == "carousel":
        return "carousel", _FORMAT_BLOCKS["carousel"]
    if content_type == "reel":
        return "reel", _FORMAT_BLOCKS["reel_short"]
    if content_type == "long_form_video":
        return "youtube_short", _FORMAT_BLOCKS["youtube_short"]
    if content_type == "long":
        if platform_hint == "linkedin":
            return "long_linkedin", _FORMAT_BLOCKS["long_linkedin"]
        return "long_advice", _FORMAT_BLOCKS["long_reddit_quora_nextdoor_medium"]
    # fall-through: treat as pinterest
    return "pinterest", _FORMAT_BLOCKS["pinterest"]


def _reference_sample() -> str:
    # User has not yet supplied a reference. Placeholder keeps prompt structurally
    # valid; swap to the real sample when provided.
    return "(no reference sample supplied yet — generate from the voice rules above)"


def _build_prompt(content_type: str, pain_point: dict[str, Any], platform_hint: str) -> tuple[str, str]:
    template = _load_prompt_template()
    format_name, format_block = _format_for_content_type(content_type, platform_hint)
    user_prompt = template.format(
        platform=platform_hint,
        pain_point=f"{pain_point['title']} — {pain_point['context']}",
        format_name=format_name,
        format_block=format_block,
        reference_sample=_reference_sample(),
    )
    system_prompt = (
        "You are a copywriter producing social content for Ominvo, a review-"
        "management SaaS for local SMBs. Follow the voice and format rules "
        "exactly. Output only the finished post body."
    )
    return system_prompt, user_prompt


def _stub_content(content_type: str, pain_point: dict[str, Any]) -> str:
    """Deterministic placeholder text for --dry-run."""
    h = hashlib.sha1(f"{content_type}:{pain_point['id']}".encode()).hexdigest()[:8]
    return (
        f"[DRY-RUN STUB {h}] {content_type} on: {pain_point['title']}.\n"
        "Three rules that actually work:\n"
        "1. Reply within 48 hours. The algorithm reads response time as owner engagement.\n"
        "2. Answer the complaint, then the bystander. Only the next reader converts.\n"
        "3. Ask for the review at receipt time. Later channels decay to single digits.\n"
        "Reply fast, write for the reader, ask at the peak.\n"
        "I built a tool (Ominvo — ominvo.com) that helps small businesses do this "
        "without hiring an agency, but the advice above works whether you use it or not."
    )


def _pool_for(content_type: str, platform_hint: str, has_hook: bool) -> str:
    """Pick the text router pool by content shape.

    hook      — short punchy single-post / short-form platforms with a hook
    long_form — long-form essays (Reddit, LinkedIn, Medium) + the "long" ctype
    default   — everything else (tweets, reels, carousels)
    """
    if content_type == "long" or platform_hint in LONG_FORM_PLATFORMS:
        return "long_form"
    if has_hook and platform_hint in SHORT_FORM_PLATFORMS:
        return "hook"
    return "default"


async def _generate_one(content_type: str, pain_point: dict[str, Any],
                        platform_hint: str, *, dry_run: bool,
                        seed: int,
                        hook: str | None = None) -> tuple[str, str]:
    """Return (caption, provider_key). provider_key='stub' in dry-run."""
    if dry_run:
        return _stub_content(content_type, pain_point), "stub"
    # Lazy import so dry-run works without shared.llm deps installed.
    from src.router.text_router import generate as route_text

    pool = _pool_for(content_type, platform_hint, hook is not None)

    # Short-form platforms get a hook-anchored prompt that expands one line
    # from short_form_hooks.txt into a platform-shaped body. Long-form
    # platforms get the full advice_post.txt template.
    if platform_hint in SHORT_FORM_PLATFORMS and hook:
        system = (
            "You are Dave Fieldston @ Ominvo. Expand the hook below into a "
            f"short post for {platform_hint}. Match the voice of the advice-"
            "post samples: short sentences, specific thresholds, mechanism-"
            "level why, no emojis, no hashtags. Close with the disclosed "
            "Ominvo mention. Output only the finished post body."
        )
        user = f"HOOK: {hook}\nPAIN POINT: {pain_point['title']} — {pain_point['context']}"
        return await route_text(user, seed=seed, pool=pool, system=system,
                                temperature=0.7, max_tokens=800)

    system, user = _build_prompt(content_type, pain_point, platform_hint)
    return await route_text(user, seed=seed, pool=pool, system=system,
                            temperature=0.7, max_tokens=1200)


async def generate_daily(*, dry_run: bool = False, seed: int | None = None) -> list[ContentItem]:
    """Generate one day's content queue: 2 reels + 1 carousel + 1 long + 3 tweets."""
    require_bot_id({"bot2"})
    rng = random.Random(seed)
    points = load_pain_points()
    if not points:
        raise RuntimeError("prompts/pain_points.json is empty")

    plan: list[tuple[str, str]] = [
        ("reel",     "instagram"),
        ("reel",     "tiktok"),
        ("carousel", "instagram"),
        ("long",     "linkedin"),
        ("tweet",    "x"),
        ("tweet",    "bluesky"),
        ("tweet",    "threads"),
    ]

    hooks = load_short_hooks()
    items: list[ContentItem] = []
    chosen_points = rng.sample(points, k=min(len(plan), len(points)))
    # Base seed for this run; each content item gets base_seed + i so the
    # provider rotation is deterministic within a run but varies across items.
    base_seed = seed if seed is not None else rng.randint(0, 2**31 - 1)
    for i, (ctype, platform_hint) in enumerate(plan):
        pp = chosen_points[i % len(chosen_points)]
        hook = rng.choice(hooks) if hooks and platform_hint in SHORT_FORM_PLATFORMS else None
        item_seed = base_seed + i
        caption, provider = await _generate_one(
            ctype, pp, platform_hint,
            dry_run=dry_run, hook=hook, seed=item_seed,
        )
        items.append(ContentItem(
            content_type=ctype,
            caption=caption,
            media_urls=[],
            target_platforms=list(TARGET_PLATFORMS.get(ctype, [])),
            pain_point_id=pp["id"],
            text_provider=provider,
        ))
        log.info("content_generated",
                 extra={"content_type": ctype, "pain_point": pp["id"],
                        "chars": len(caption), "dry_run": dry_run,
                        "provider": provider, "seed": item_seed})
    return items


# --- CLI --------------------------------------------------------------------

def _main_cli() -> int:
    ap = argparse.ArgumentParser(description="Bot 2 daily content generator")
    ap.add_argument("--dry-run", action="store_true", help="skip LLM, emit stubs")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    items = asyncio.run(generate_daily(dry_run=args.dry_run, seed=args.seed))
    for it in items:
        print(f"--- {it.content_type} [{it.pain_point_id}] -> {','.join(it.target_platforms)}")
        print(it.caption)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main_cli())
