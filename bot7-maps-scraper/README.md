# Bot 7 — Local Business Lead Factory

Scrapes Google Maps across **32 US metros × 6 verticals = 192 segments**, enriches with
Hunter.io, and writes one CSV pack per segment for sale on Gumroad / Payhip / Whop.

Goal: first **10 packs live by Nov 30**.

## Isolation

- Lives in its own folder: `bot7-maps-scraper/` with its own `.env`, `pyproject.toml`,
  and `workers/nightly_run.py`. `shared.config.require_bot_id("bot7")` enforces this at
  import time.
- Writes to the **same Supabase project** as Bot 1, but every table is prefixed `bot7_`
  (`bot7_businesses`, `bot7_runs`, `bot7_packs`).
- Playwright sessions are stored under `~/.bot_sessions/bot7/` — no overlap with Bot 1/2.
- GHA cron runs at **03:00 UTC** (staggered from Bot 1 @ 02 / Bot 2 @ 15).

## Quick start

```bash
cd bot7-maps-scraper
cp .env.example .env     # fill in creds (reuse Bot 1 values)
poetry install
poetry run playwright install chromium
# Smoke test: one metro × one vertical, DRY_RUN=true (no DB writes)
poetry run python scripts/test_single_metro.py nyc restaurants
```

CSV lands in `output/YYYY-MM-DD/nyc__restaurants.csv`.

## Full nightly

```bash
BOT7_DRY_RUN=false poetry run python -m workers.nightly_run
```

Also fires on GHA via `.github/workflows/bot7-nightly.yml`.

## Supabase

Apply the migration once:

```bash
psql "$DATABASE_URL" -f supabase/migrations/001_bot7_init.sql
```

## GHA secrets

All new, all reuse Bot 1 values:

- `BOT7_DATABASE_URL`
- `BOT7_SUPABASE_URL`
- `BOT7_SUPABASE_ANON_KEY`
- `BOT7_SUPABASE_SERVICE_ROLE_KEY`
- `BOT7_HUNTER_API_KEY`
- `BOT7_TELEGRAM_BOT_TOKEN`
- `BOT7_TELEGRAM_CHAT_ID`

## Pipeline

1. **Scrape** Google Maps per segment (cap 120, chain-excluded)
2. **Enrich** with Hunter.io (cap 40 lookups/segment → ~7,700/run worst case)
3. **Dedup** on `(segment_slug, lower(name)|lower(address[:40]))`
4. **CSV** → `output/YYYY-MM-DD/{metro}__{vertical}.csv`
5. **Upsert** to `bot7_businesses`; log run to `bot7_runs`
6. **Telegram digest** to the Ominvo command bot

## Risks

- **Google Maps soft-block** → stealth persistent context + 30s inter-segment sleep.
  If one metro walls, run isolates it in `PhaseTracker` and keeps going.
- **Hunter quota** → hard cap at 40 lookups/segment.
- **Run time** ~5 hrs → fine for nightly; shard the matrix into a GHA job matrix later
  if it starts bothering us.
