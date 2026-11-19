"""Env loader. Walks up from cwd looking for the nearest .env, loads it once."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

_LOADED = False


def _find_dotenv(start: Optional[Path] = None) -> Optional[Path]:
    cur = (start or Path.cwd()).resolve()
    for parent in (cur, *cur.parents):
        candidate = parent / ".env"
        if candidate.is_file():
            return candidate
    return None


def load_env(start: Optional[Path] = None, override: bool = False) -> Optional[Path]:
    """Load the nearest .env walking up from `start` (or cwd). Idempotent."""
    global _LOADED
    if _LOADED and not override:
        return None
    path = _find_dotenv(start)
    if path is not None:
        load_dotenv(path, override=override)
    _LOADED = True
    return path


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
    """Our convention for 'not yet wired': value begins with TODO_."""
    if value is None:
        return True
    return value.strip() == "" or value.strip().startswith("TODO_")
