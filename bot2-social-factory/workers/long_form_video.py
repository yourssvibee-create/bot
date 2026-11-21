"""Long-form (6-10 min) landscape video builder.

Pipeline: Groq generates ~1800-word script → split into ~30s segments →
Edge-TTS per segment → Pexels b-roll per segment (or placeholder) →
ffmpeg stitch. Triggered every 3rd day by nightly_run.

CLI:
  python -m workers.long_form_video --dry-run --date 2026-11-23
"""
from __future__ import annotations

import argparse
import asyncio
import re
from datetime import date, datetime
from pathlib import Path

from shared.config import require_bot_id
from shared.logging import get_logger

from src import content_generator as cg
from src.media.typography import phrases_from_script, render_typography_video
from src.video_pipeline import (VideoSegment, fetch_pixabay_clip,
                                make_placeholder_clip, probe_duration,
                                render_root, stitch, synthesize_tts)

log = get_logger("bot2.long_form")

SEG_WORDS = 70  # ~30s of narration at ~140wpm

# See workers/short_form_video.py for rationale.
_PIXABAY_TRIGGERS = {
    "restaurant", "cafe", "office", "store", "laptop", "phone",
    "meeting", "team", "customer", "shop", "kitchen", "storefront",
}


def _needs_real_footage(script: str) -> bool:
    low = script.lower()
    return any(t in low for t in _PIXABAY_TRIGGERS)


def should_run_today(d: date) -> bool:
    """Every 3rd day by ordinal: Nov 22 → Nov 25 → Nov 28 …"""
    return d.toordinal() % 3 == 0


def _slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:48] or "ominvo-long"


def _script_stub(pain_point: dict) -> str:
    """Compact deterministic script for dry-run. Real runs call Groq for ~1800
    words; dry-run stays tiny so smoke tests finish in <60s."""
    rules = (
        "Reply within 48 hours or Google treats you as inactive. "
        "Response rate is the hidden local-ranking tiebreaker. "
        "Photo count outranks half a star of rating. "
    )
    body = f"Today's topic: {pain_point['title']}. {pain_point['context']} "
    return (body + rules) * 2


def split_segments(script: str) -> list[str]:
    words = script.split()
    out = []
    for i in range(0, len(words), SEG_WORDS):
        out.append(" ".join(words[i:i + SEG_WORDS]))
    return [s for s in out if s.strip()]


async def _generate_script(pain_point: dict, dry_run: bool,
                           seed: int) -> tuple[str, str]:
    """Return (script, provider_key). provider='stub' in dry-run."""
    if dry_run:
        return _script_stub(pain_point), "stub"
    from src.router.text_router import generate as route_text
    system = (
        "You are Dave Fieldston @ Ominvo. Write a 1800-word video script on "
        "the given topic in your contrarian-but-kind voice. Plain prose, no "
        "headers, no bullet points, no emojis. Specific thresholds. Include "
        "a disclosed Ominvo close."
    )
    user = f"TOPIC: {pain_point['title']}\n{pain_point['context']}"
    return await route_text(user, seed=seed, pool="long_form", system=system,
                            temperature=0.6, max_tokens=4000)


async def build_long(*, dry_run: bool, as_of: date) -> Path:
    require_bot_id({"bot2"})
    points = cg.load_pain_points()
    # Deterministic pain-point pick from the date ordinal.
    pp = points[as_of.toordinal() % len(points)]

    # Seed off the date ordinal so reruns are deterministic.
    seed = as_of.toordinal()
    script, text_provider = await _generate_script(pp, dry_run=dry_run, seed=seed)
    segments = split_segments(script)
    log.info("long_form_segments", extra={
        "n": len(segments), "pp": pp["id"], "provider": text_provider,
    })

    out_dir = render_root("long_form")
    slug = _slugify(pp["title"])
    out_path = out_dir / f"{as_of.isoformat()}_{slug}.mp4"

    seg_objects: list[VideoSegment] = []
    for i, seg_text in enumerate(segments):
        audio_path = out_dir / f".{slug}_seg{i:03d}.mp3"
        try:
            await synthesize_tts(seg_text, audio_path)
        except Exception as e:
            if dry_run:
                log.warning("tts_skipped_dry",
                            extra={"err": str(e)[:200], "seg": i})
                import subprocess
                subprocess.run([
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                    "-t", "2", "-c:a", "aac", str(audio_path),
                ], check=False)
            else:
                raise
        duration = probe_duration(audio_path) or 2.0

        broll_path = out_dir / f".{slug}_broll{i:03d}.mp4"
        visual_source = "placeholder"
        use_pixabay = _needs_real_footage(seg_text)

        if not dry_run and use_pixabay:
            try:
                clip = await fetch_pixabay_clip(pp["title"], broll_path)
                if clip is not None:
                    visual_source = "pixabay"
            except Exception as e:
                log.warning("pixabay_fail", extra={"err": str(e)[:200], "seg": i})

        if not dry_run and visual_source == "placeholder":
            try:
                phrases = phrases_from_script(seg_text, duration)
                # landscape 1920x1080 for long-form
                render_typography_video(phrases, audio_path, broll_path,
                                        size=(1920, 1080))
                visual_source = "typography"
            except Exception as e:
                log.warning("typography_fail",
                            extra={"err": str(e)[:200], "seg": i})

        if visual_source == "placeholder":
            # alternate two colors so the placeholder isn't one solid frame
            color = "0x0f172a" if i % 2 == 0 else "0x1e293b"
            make_placeholder_clip(broll_path, duration, color=color)

        log.debug("long_form_seg_visual",
                  extra={"seg": i, "source": visual_source})

        seg_objects.append(VideoSegment(
            text=seg_text, audio_path=audio_path, broll_path=broll_path,
            duration=duration,
        ))

    stitch(seg_objects, out_path, orientation="landscape")
    log.info("long_form_rendered", extra={"path": str(out_path),
                                          "n_segments": len(seg_objects)})
    return out_path


def _main_cli() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--date", default=date.today().isoformat())
    args = ap.parse_args()
    d = datetime.fromisoformat(args.date).date()
    if not args.dry_run and not should_run_today(d):
        print(f"skipped: {d} is not a long-form trigger day (ordinal % 3 != 0)")
        return 0
    out = asyncio.run(build_long(dry_run=args.dry_run, as_of=d))
    print(f"RENDERED: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main_cli())
