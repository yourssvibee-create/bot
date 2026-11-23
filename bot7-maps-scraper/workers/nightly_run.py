"""Nightly entrypoint for GHA cron. Delegates to src.main."""
from __future__ import annotations

import sys
from pathlib import Path

_BOT_ROOT = Path(__file__).resolve().parents[1]
if str(_BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(_BOT_ROOT))

from src.main import main

if __name__ == "__main__":
    raise SystemExit(main())
