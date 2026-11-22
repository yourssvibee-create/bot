"""Apply Bot 2's SQL migrations to DATABASE_URL.

Idempotent: migrations use `create table if not exists`, so re-running is
safe. Runs every .sql file under bot2-social-factory/supabase/migrations/ in
filename-sorted order and prints a one-line summary per file.

Invoked from the nightly workflow BEFORE the orchestrator so the schema is
guaranteed present before any INSERT runs. Can also be run by hand:

    python -m scripts.apply_migrations
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make `shared/` importable when run from bot2-social-factory/.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.db import connect  # noqa: E402
from shared.logging import configure, get_logger  # noqa: E402

configure()
log = get_logger("bot2.migrations")

_MIG_DIR = Path(__file__).resolve().parents[1] / "supabase" / "migrations"


def main() -> int:
    if not _MIG_DIR.is_dir():
        print(f"no migrations dir at {_MIG_DIR}", flush=True)
        return 0
    files = sorted(p for p in _MIG_DIR.iterdir()
                   if p.is_file() and p.suffix == ".sql")
    if not files:
        print("no .sql migrations found", flush=True)
        return 0

    conn = connect()
    try:
        with conn.transaction():
            for f in files:
                sql = f.read_text(encoding="utf-8")
                conn.execute(sql)
                print(f"applied {f.name} ({len(sql)} bytes)", flush=True)
                log.info("migration_applied", extra={"file": f.name})
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
