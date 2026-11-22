"""Env loader with strict per-bot .env isolation.

Rules (non-negotiable; API keys are per-unit):

1. The `.env` MUST live in the bot folder itself (same directory as
   `pyproject.toml`). We do NOT walk up the tree, and we do NOT fall back
   to a repo-root `.env`.
2. If a `.env` exists at the repo root (directory containing the
   `.github/` folder or marked by `BOT_REPO_ROOT`), we refuse to load —
   that would silently mix keys across units.
3. Every bot sets `BOT_ID=<botN>` in its own `.env`; `require_bot_id()`
   enforces that callers can't accidentally run outside of a declared bot.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Optional

from dotenv import load_dotenv

_LOADED = False
_LOADED_FROM: Optional[Path] = None

# Known bot IDs. Add new bots here when they ship.
KNOWN_BOT_IDS: set[str] = {
    "bot1", "bot2", "bot3", "bot4", "bot5", "bot6", "bot7", "bot8",
    "bot9", "bot10", "bot11", "bot12", "bot13", "bot14", "bot16",
    "bot17", "ominvo-leadgen",
}

# Markers that indicate a repo-root directory (where a .env is FORBIDDEN).
_REPO_ROOT_MARKERS = (".github", ".git")

# Markers that indicate a bot-folder directory (where the .env SHOULD live).
_BOT_ROOT_MARKERS = ("pyproject.toml",)


class EnvIsolationError(RuntimeError):
    """Raised when .env is in the wrong place or missing."""


def _is_repo_root(p: Path) -> bool:
    return any((p / m).exists() for m in _REPO_ROOT_MARKERS)


def _is_bot_root(p: Path) -> bool:
    return any((p / m).exists() for m in _BOT_ROOT_MARKERS)


def _find_bot_root(start: Path) -> Optional[Path]:
    """Walk up looking for the nearest directory with pyproject.toml.

    Stops as soon as it hits a repo-root marker (so a pyproject.toml at the
    repo root itself wouldn't count — bots must have their own folder).
    """
    cur = start.resolve()
    for p in (cur, *cur.parents):
        if _is_repo_root(p):
            return None
        if _is_bot_root(p):
            return p
    return None


def _find_repo_root(start: Path) -> Optional[Path]:
    cur = start.resolve()
    for p in (cur, *cur.parents):
        if _is_repo_root(p):
            return p
    return None


def load_env(start: Optional[Path] = None, *, override: bool = False) -> Optional[Path]:
    """Load the bot-folder `.env`. Idempotent.

    Refuses to load when:
      * no bot folder is above `start`;
      * the bot folder has no `.env`;
      * a `.env` also exists at the repo root (ambiguous → unsafe).
    """
    global _LOADED, _LOADED_FROM
    if _LOADED and not override:
        return _LOADED_FROM

    # CI mode: when the host has already injected critical env vars
    # (GitHub Actions `env:` block, container secrets), skip the .env file
    # requirement and use os.environ directly. The isolation rule still
    # holds in local dev where these aren't preset.
    if any(os.environ.get(k) for k in ("GROQ_API_KEY", "DATABASE_URL")):
        _LOADED = True
        _LOADED_FROM = None
        return None

    start_path = (start or Path.cwd()).resolve()

    # 1. Repo-root safety check — refuse if a stray .env sits at the root.
    repo_root = _find_repo_root(start_path)
    if repo_root is not None:
        stray = repo_root / ".env"
        if stray.is_file():
            raise EnvIsolationError(
                f"Repo-root .env detected at {stray} — delete it. "
                "API keys live in each bot's own .env, never shared."
            )

    # 2. Find the bot folder and require its .env.
    bot_root = _find_bot_root(start_path)
    if bot_root is None:
        raise EnvIsolationError(
            f"No bot folder found above {start_path}. Each bot runs from its "
            "own folder (the one containing pyproject.toml)."
        )
    env_path = bot_root / ".env"
    if not env_path.is_file():
        raise EnvIsolationError(
            f"No .env in {bot_root}. Each bot has its own .env with its own "
            "API keys. Keys must NEVER be at the repo root."
        )

    load_dotenv(env_path, override=override)
    _LOADED = True
    _LOADED_FROM = env_path
    return env_path


def get(name: str, default: Optional[str] = None, *, required: bool = False) -> Optional[str]:
    load_env()
    val = os.environ.get(name, default)
    if required and (val is None or val == ""):
        raise RuntimeError(f"Missing required env var: {name}")
    return val


def get_int(name: str, default: int) -> int:
    v = get(name)
    if v is None or v == "":
        return default
    return int(v)


def get_bool(name: str, default: bool = False) -> bool:
    v = get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def is_placeholder(value: Optional[str]) -> bool:
    """Our convention for 'not yet wired': value is empty or begins with TODO_."""
    if value is None:
        return True
    v = value.strip()
    return v == "" or v.startswith("TODO_") or v == "PASTE_ME"


def require_bot_id(allowed: Optional[Iterable[str]] = None) -> str:
    """Return BOT_ID, raising EnvIsolationError if missing/unknown.

    Pass `allowed` to constrain to a specific set (e.g. a lib that only
    supports a subset of bots). Default: any value in `KNOWN_BOT_IDS`.
    """
    load_env()
    bot_id = (os.environ.get("BOT_ID") or "").strip()
    whitelist = set(allowed) if allowed is not None else KNOWN_BOT_IDS
    if not bot_id:
        raise EnvIsolationError(
            "BOT_ID is unset. Each bot declares BOT_ID in its own .env "
            "(e.g. BOT_ID=bot1). This prevents cross-bot key leakage."
        )
    if bot_id not in whitelist:
        raise EnvIsolationError(
            f"Unknown BOT_ID={bot_id!r}. Add it to shared.config.KNOWN_BOT_IDS "
            "once the bot folder exists. Known: "
            f"{sorted(whitelist)}"
        )
    return bot_id


def current_bot_id_or_none() -> Optional[str]:
    """Non-raising variant — returns None if BOT_ID is missing/unknown.

    Useful for utility code like the LLM router's log-line enrichment where
    we want to tag the bot but a missing tag shouldn't crash the call.
    """
    try:
        return require_bot_id()
    except EnvIsolationError:
        return None
