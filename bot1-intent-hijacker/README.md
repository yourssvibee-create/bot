# Bot 1 — Intent Hijacker

Scrapes public buying-intent signals (Trustpilot 1–3★ complaints, Quora reputation
questions, Nitter/X mentions, Reddit threads, Bot 7's local-maps feed), scores
them with Groq (Llama 3.1 8B), drafts a disclosed-Ominvo reply with Groq
(Llama 3.3 70B), queues it for Telegram approval, then dispatches via
contact form or social reply. Email-only leads are handed off to `ominvo-leadgen`.

Persona: **Dave Fieldston, Marketing @ Ominvo** — reply framing is always
first-person *"I work on Ominvo, here's how we approach this..."*, never
third-party testimonial.

## Status

* Reddit: karma-gated on `dave_fieldston` for ~2 weeks. Set
  `REDDIT_CLIENT_ID=TODO_karma_gate` and the scraper + dispatch stubs
  cleanly skip Reddit and log once.
* Email dispatch: **not here**. Bot 1 writes to `handoff_to_leadgen` instead;
  `ominvo-leadgen` handles outbound email.

## Run locally

```bash
cd bot1-intent-hijacker
poetry install
poetry run playwright install chromium
cp .env.example .env     # then paste real keys
poetry run python -m workers.nightly_run
```

## Scheduling

Production cron runs nightly on **GitHub Actions** (`.github/workflows/bot1-nightly.yml`,
02:00 UTC). Vercel hosts only the Telegram approval webhook — not the cron.

## Data

All tables live in the Unit 1 Supabase project (shared with Bot 2). Schema
in [`sql/schema.sql`](sql/schema.sql). The `bot7_leads` table is read-only
for Bot 1; Bot 7 populates it.

## Rules

* Never commit `.env`. Only `.env.example`.
* Every outbound call is tenacity-retried (max 3, exp backoff).
* Every scraper polite-delays. Defaults: Trustpilot 4s, Quora 3s, Nitter 2s.
* Every dispatch, skip, and error is logged (JSON lines) and persisted to
  `dispatch_log`.
