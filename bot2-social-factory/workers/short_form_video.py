"""Short-form (30-60s) vertical video builder.

Pipeline: hook from short_form_hooks.txt → Groq expansion to ~15-30s script
→ Edge-TTS voiceover → Pexels b-roll (or placeholder) → ffmpeg stitch.

CLI:
  python -m workers.short_form_video --dry-run --slug ominvo_reel_01
"""
from __future__ import annotations

import argparse
import asyncio
import random
import re
from datetime import date
from pathlib import Path

from shared.config import require_bot_id
from shared.logging import get_logger

from src import content_generator as cg
from src.video_pipeline import (VideoSegment, fetch_pixabay_clip,
                                make_placeholder_clip, probe_duration,
                                render_root, stitch, synthesize_tts)

log = get_logger("bot2.short_form")


def _slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:48] or "ominvo-short"


async def build_short(*, dry_run: bool, slug: str | None, seed: int | None) -> Path:
    require_bot_id({"bot2"})
    rng = random.Random(seed)
    hooks = cg.load_short_hooks()
    if not hooks:
        raise RuntimeError("short_form_hooks.txt is empty")
    hook = rng.choice(hooks)
    body = (
        f"{hook}\n\n"
        "Reply fast. Keep it short. Specific thresholds beat vague advice. "
        "I built Ominvo for exactly this — ominvo.com."
    )
    log.info("short_form_script", extra={"hook": hook[:60], "chars": len(body)})

    out_dir = render_root("short_form")
    slug = _slugify(slug or hook)
    out_path = out_dir / f"{date.today().isoformat()}_{slug}.mp4"

    # 1. TTS
    audio_path = out_dir / f".{slug}.mp3"
    try:
        await synthesize_tts(body, audio_path)
    except Exception as e:
        if dry_run:
            log.warning("tts_skipped_dry", extra={"err": str(e)[:200]})
            # Write a 2-second silent audio as placeholder so stitch still runs.
            import subprocess
            subprocess.run([
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                "-t", "2", "-c:a", "aac", str(audio_path),
            ], check=False)
        else:
            raise
    duration = probe_duration(audio_path) or 2.0

    # 2. b-roll (Pexels or placeholder)
    broll_path = out_dir / f".{slug}_broll.mp4"
    clip = None
    if not dry_run:
        try:
            clip = await fetch_pixabay_clip(hook, broll_path)
        except Exception as e:
            log.warning("pixabay_fail", extra={"err": str(e)[:200]})
    if clip is None:
        make_placeholder_clip(broll_path, duration, width=1080, height=1920)

    # 3. stitch
    seg = VideoSegment(text=body, audio_path=audio_path, broll_path=broll_path,
                       duration=duration)
    stitch([seg], out_path, orientation="portrait")
    log.info("short_form_rendered", extra={"path": str(out_path),
                                           "duration": duration})
    return out_path


def _main_cli() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--slug", default=None)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    out = asyncio.run(build_short(dry_run=args.dry_run, slug=args.slug, seed=args.seed))
    print(f"RENDERED: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main_cli())
