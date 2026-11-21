"""Kinetic typography — primary visual path for short-form video.

Generates a vertical 9:16 mp4 of brand-styled animated text synced to an
audio track, using only ffmpeg drawtext filters. Zero external media deps
and no API calls — highest authenticity signal across platform classifiers.

Phrases arrive as (text, start_sec, end_sec) tuples; each phrase fades in
and out inside its window. Accent color alternates every third phrase for
visual rhythm.
"""
from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

from shared.config import get
from shared.logging import get_logger

log = get_logger("bot2.typography")

BRAND_COLORS = {
    "bg":     "0x0A0E1A",  # deep navy
    "fg":     "0xFFFFFF",  # white
    "accent": "0x00D9A3",  # ominvo teal
}

# Font resolution order:
#   1. TYPOGRAPHY_FONT env var (explicit override)
#   2. Linux DejaVu (CI / nightly workflow)
#   3. Windows Arial Bold (local dev)
_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
]


def _resolve_font() -> str:
    override = get("TYPOGRAPHY_FONT")
    if override and os.path.exists(override):
        return override
    for p in _FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    raise FileNotFoundError(
        "No typography font found. Install DejaVu (Linux) or set TYPOGRAPHY_FONT."
    )


def _escape_drawtext(s: str) -> str:
    """Escape chars that ffmpeg's drawtext filter parses specially."""
    return (
        s.replace("\\", r"\\")
         .replace("'", r"\'")
         .replace(":", r"\:")
         .replace(",", r"\,")
         .replace("%", r"\%")
    )


def _ffmpeg_path(font_path: str) -> str:
    r"""Normalise the font path so ffmpeg's drawtext filter can parse it on
    Windows — forward slashes and the ``C\:`` colon escape."""
    p = font_path.replace("\\", "/")
    if len(p) > 1 and p[1] == ":":
        p = p[0] + "\\:" + p[2:]
    return p


def render_typography_video(
    phrases: list[tuple[str, float, float]],
    audio_path: str | Path,
    out_path: str | Path,
    *,
    size: tuple[int, int] = (1080, 1920),
    font_size: int = 72,
    wrap_chars: int = 18,
) -> Path:
    """Render a kinetic typography video. Returns `out_path` as Path on success."""
    w, h = size
    font = _ffmpeg_path(_resolve_font())
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    filters: list[str] = []
    for i, (txt, start, end) in enumerate(phrases):
        if end <= start:
            continue
        wrapped = r"\n".join(textwrap.wrap(txt, width=wrap_chars)) or _escape_drawtext(txt)
        esc = _escape_drawtext(wrapped) if wrapped is txt else wrapped
        color = BRAND_COLORS["accent"] if i % 3 == 0 else BRAND_COLORS["fg"]
        fade_in_end = min(start + 0.1, end)
        fade_out_start = max(end - 0.2, start)
        filters.append(
            f"drawtext=fontfile='{font}'"
            f":text='{esc}'"
            f":fontsize={font_size}"
            f":fontcolor={color}"
            f":x=(w-text_w)/2:y=(h-text_h)/2"
            f":enable='between(t,{start:.2f},{end:.2f})'"
            f":alpha='if(lt(t,{fade_in_end:.2f}),(t-{start:.2f})/0.1,"
            f"if(gt(t,{fade_out_start:.2f}),({end:.2f}-t)/0.2,1))'"
        )

    total_duration = max((end for _, _, end in phrases), default=5.0) + 0.5
    filter_graph = ",".join(filters) if filters else "null"

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i",
        f"color=c={BRAND_COLORS['bg']}:s={w}x{h}:d={total_duration:.2f}",
        "-i", str(audio_path),
        "-filter_complex", f"[0:v]{filter_graph}[v]",
        "-map", "[v]", "-map", "1:a",
        "-shortest",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        str(out_path),
    ]
    log.info("typography_render", extra={
        "phrases": len(phrases),
        "duration": total_duration,
        "out": str(out_path),
    })
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        log.error("typography_ffmpeg_fail", extra={"stderr": r.stderr[-500:]})
        raise RuntimeError("typography render failed")
    return out_path


def phrases_from_script(script: str, total_duration: float) -> list[tuple[str, float, float]]:
    """Split a narration script into timed phrases for drawtext.

    Splits on sentence boundaries, then distributes `total_duration` across
    phrases proportional to character count so longer phrases stay on screen
    longer. Pure heuristic — good enough as a default when caller doesn't have
    whisper word-level timestamps.
    """
    import re
    parts = [s.strip() for s in re.split(r"(?<=[.!?])\s+", script) if s.strip()]
    if not parts:
        return []
    total_chars = sum(len(p) for p in parts) or 1
    out: list[tuple[str, float, float]] = []
    t = 0.0
    for p in parts:
        dur = max(1.2, total_duration * (len(p) / total_chars))
        out.append((p, t, t + dur))
        t += dur
    return out
