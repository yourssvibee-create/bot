"""Bot 7 settings. Loads via shared.config (strict per-bot .env isolation)."""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dataclasses import dataclass

from shared.config import get, load_env, require_bot_id

load_env()
require_bot_id({"bot7"})


def _bool(v: str | None, default: bool = False) -> bool:
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _int(v: str | None, default: int) -> int:
    try:
        return int(v) if v is not None else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    database_url: str
    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: str
    hunter_api_key: str
    telegram_bot_token: str
    telegram_chat_id: str
    dry_run: bool
    hunter_cap_per_segment: int
    inter_segment_sleep_s: int
    scrape_cap_per_segment: int
    output_dir: str
    metros_filter: str
    verticals_filter: str
    gumroad_access_token: str
    payhip_api_key: str
    whop_api_key: str
    channels_enabled: str      # csv of {gumroad,payhip,whop}; empty = all configured
    min_records_for_ready: int
    sample_pack_rows: int
    listing_publish: bool      # when false, products land as draft/hidden on all channels
    scroll_max_rounds: int
    scroll_plateau_threshold: int
    scroll_pause_ms: int


settings = Settings(
    database_url=get("DATABASE_URL", default="") or "",
    supabase_url=get("SUPABASE_URL", default="") or "",
    supabase_anon_key=get("SUPABASE_ANON_KEY", default="") or "",
    supabase_service_role_key=get("SUPABASE_SERVICE_ROLE_KEY", default="") or "",
    hunter_api_key=get("HUNTER_API_KEY", default="") or "",
    telegram_bot_token=get("TELEGRAM_BOT_TOKEN", default="") or "",
    telegram_chat_id=get("TELEGRAM_CHAT_ID", default="") or "",
    dry_run=_bool(get("BOT7_DRY_RUN"), default=True),
    hunter_cap_per_segment=_int(get("BOT7_HUNTER_CAP_PER_SEGMENT"), 40),
    inter_segment_sleep_s=_int(get("BOT7_INTER_SEGMENT_SLEEP_S"), 30),
    scrape_cap_per_segment=_int(get("BOT7_SCRAPE_CAP_PER_SEGMENT"), 120),
    output_dir=get("BOT7_OUTPUT_DIR", default="output") or "output",
    metros_filter=get("BOT7_METROS_FILTER", default="") or "",
    verticals_filter=get("BOT7_VERTICALS_FILTER", default="") or "",
    gumroad_access_token=get("BOT7_GUMROAD_ACCESS_TOKEN", default="") or "",
    payhip_api_key=get("BOT7_PAYHIP_API_KEY", default="") or "",
    whop_api_key=get("BOT7_WHOP_API_KEY", default="") or "",
    channels_enabled=get("BOT7_CHANNELS_ENABLED", default="") or "",
    min_records_for_ready=_int(get("BOT7_MIN_RECORDS_FOR_READY"), 20),
    sample_pack_rows=_int(get("BOT7_SAMPLE_PACK_ROWS"), 10),
    listing_publish=_bool(get("BOT7_LISTING_PUBLISH"), default=False),
    scroll_max_rounds=_int(get("BOT7_SCROLL_MAX_ROUNDS"), 60),
    scroll_plateau_threshold=_int(get("BOT7_SCROLL_PLATEAU"), 2),
    scroll_pause_ms=_int(get("BOT7_SCROLL_PAUSE_MS"), 1500),
)

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
