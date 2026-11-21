"""Shared video pipeline helpers for short_form_video and long_form_video.

Zero-cost stack: Groq → Edge-TTS → Pexels b-roll → ffmpeg → faster-whisper.

Any missing piece (PEXELS_API_KEY, ffmpeg binary, whisper model) is logged
and the pipeline substitutes a placeholder path so the overall render still
produces a valid mp4 (useful for dry-runs and fault-tolerant production).
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from shared.config import get
from shared.logging import get_logger

log = get_logger("bot2.video")

_RENDER_ROOT = Path(__file__).resolve().parents[1] / ".renders"


class FFmpegMissing(RuntimeError):
    pass


def require_ffmpeg() -> str:
    """Resolve the ffmpeg binary at import-side callers. Raise with install hint."""
    p = shutil.which("ffmpeg")
    if p is None:
        raise FFmpegMissing(
            "ffmpeg not on PATH. Install:\n"
            "  Windows: winget install ffmpeg  (or choco install ffmpeg)\n"
            "  macOS:   brew install ffmpeg\n"
            "  Linux:   apt-get install -y ffmpeg"
        )
    return p


@dataclass
class VideoSegment:
    text: str           # narration text
    audio_path: Path    # Edge-TTS output
    broll_path: Path    # Pexels clip or placeholder
    duration: float     # seconds, derived from audio


async def synthesize_tts(text: str, out_path: Path, *,
                         voice: str = "en-US-GuyNeural") -> Path:
    """Synthesize `text` to mp3 with Edge-TTS."""
    try:
        import edge_tts  # type: ignore
    except ImportError:
        raise RuntimeError("edge-tts not installed; run: pip install edge-tts")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    communicate = edge_tts.Communicate(text, voice=voice)
    await communicate.save(str(out_path))
    return out_path


def probe_duration(path: Path) -> float:
    """Return media duration in seconds via ffprobe. Returns 0 on failure."""
    try:
        out = subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ], text=True, timeout=10)
        return float(out.strip() or 0)
    except Exception:
        return 0.0


def make_placeholder_clip(out_path: Path, duration: float, *,
                          width: int = 1920, height: int = 1080,
                          color: str = "0x0f172a") -> Path:
    """Write a solid-color mp4 at `duration` seconds. Used when Pexels key
    missing or Pexels fetch fails."""
    require_ffmpeg()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c={color}:s={width}x{height}:r=30",
        "-t", f"{max(0.5, duration):.2f}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
        str(out_path),
    ], check=True)
    return out_path


def stitch(segments: Iterable[VideoSegment], out_path: Path, *,
           orientation: str = "landscape") -> Path:
    """Concatenate segments: each b-roll clip with its segment's audio dubbed
    over. Returns the final mp4 path."""
    require_ffmpeg()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    segs = list(segments)
    if not segs:
        raise RuntimeError("stitch: no segments")

    # 1. Build per-segment mp4s with audio overlaid.
    tmp_dir = out_path.parent / f".{out_path.stem}_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    part_paths: list[Path] = []
    for i, s in enumerate(segs):
        part = tmp_dir / f"part_{i:03d}.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(s.broll_path),
            "-i", str(s.audio_path),
            "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-c:a", "aac",
            "-pix_fmt", "yuv420p", "-preset", "veryfast",
            "-shortest", str(part),
        ], check=True)
        part_paths.append(part)

    # 2. Concat demuxer file.
    concat_list = tmp_dir / "concat.txt"
    concat_list.write_text(
        "\n".join(f"file '{p.as_posix()}'" for p in part_paths),
        encoding="utf-8",
    )
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p",
        "-preset", "veryfast", str(out_path),
    ], check=True)
    return out_path


def render_root(kind: str) -> Path:
    p = _RENDER_ROOT / kind
    p.mkdir(parents=True, exist_ok=True)
    return p


async def fetch_pexels_clip(query: str, out_path: Path) -> Path | None:
    """Download one Pexels video clip matching `query`. Returns None if no
    key or no result."""
    import httpx
    api_key = get("PEXELS_API_KEY")
    if not api_key:
        log.warning("pexels_no_key", extra={"query": query})
        return None
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
        r = await c.get(
            "https://api.pexels.com/videos/search",
            params={"query": query, "per_page": 1, "orientation": "landscape"},
            headers={"Authorization": api_key},
        )
        r.raise_for_status()
        data = r.json()
    videos = data.get("videos") or []
    if not videos:
        return None
    files = videos[0].get("video_files") or []
    mp4s = [f for f in files if f.get("file_type") == "video/mp4"]
    if not mp4s:
        return None
    # pick smallest (fast download) landscape clip
    chosen = min(mp4s, key=lambda f: (f.get("width", 9999)))
    url = chosen["link"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
        out_path.write_bytes(r.content)
    return out_path


async def fetch_pixabay_clip(query: str, out_path: Path) -> Path | None:
    """Download one Pixabay video clip matching `query`. Returns None if no
    key or no result. Replaces Pexels (API paused globally Nov 2026)."""
    import httpx
    api_key = get("PIXABAY_API_KEY")
    if not api_key:
        log.warning("pixabay_no_key", extra={"query": query})
        return None
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
        r = await c.get(
            "https://pixabay.com/api/videos/",
            params={
                "key": api_key,
                "q": query,
                "video_type": "film",
                "per_page": 3,
                "safesearch": "true",
            },
        )
        r.raise_for_status()
        data = r.json()
    hits = data.get("hits") or []
    if not hits:
        return None
    videos = hits[0].get("videos") or {}
    # Prefer large (1080p+), fall back to medium (720p), then small
    for tier in ("large", "medium", "small"):
        v = videos.get(tier) or {}
        url = v.get("url")
        if url:
            break
    else:
        return None
    out_path.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
        out_path.write_bytes(r.content)
    return out_path
