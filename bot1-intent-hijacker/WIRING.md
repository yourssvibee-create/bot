# Bot 1 — Wiring Steps

CLIs are installed globally (`vercel`, `supabase`). The remaining steps all
need interactive browser auth and must be done on your laptop. Run them from
`D:\bot\bot1-intent-hijacker`.

## 1. Auth

```powershell
vercel login            # choose "Continue with Email", use the Unit 1 Gmail
supabase login          # opens browser, same Unit 1 Gmail
```

## 2. Create the Supabase project

```powershell
# Confirm your region first — ask which Supabase region is nearest.
# Common picks: us-east-1, us-west-1, eu-central-1, ap-south-1, ap-southeast-1.
supabase projects create unit1-outreach --region <region>
```

Supabase prints the project ref. Then from the dashboard
(`https://supabase.com/dashboard/project/<ref>/settings/database`) grab:

* **Project ref** → `SUPABASE_URL = https://<ref>.supabase.co`
* **anon public key** → `SUPABASE_ANON_KEY`
* **Connection string (Session pooler)** → `DATABASE_URL`

Paste those into `bot1-intent-hijacker/.env` (which you create from
`.env.example`). Never commit `.env`.

Then apply the schema (from inside `bot1-intent-hijacker/`):

```powershell
supabase link --project-ref <ref>
supabase db push --file sql/schema.sql
# or paste sql/schema.sql into the dashboard SQL editor
```

## 3. Create the Vercel project

```powershell
vercel link                         # select unit1-outreach team → new project
                                    #   name: bot1-intent-hijacker
                                    #   root: . (current folder)

# Add env vars (production scope)
vercel env add GROQ_API_KEY production
vercel env add DATABASE_URL production
vercel env add TELEGRAM_BOT_TOKEN production
vercel env add TELEGRAM_CHAT_ID production
vercel env add SUPABASE_URL production
vercel env add SUPABASE_ANON_KEY production
# Reddit keys intentionally skipped — TODO_karma_gate

vercel deploy --prod
```

Vercel only hosts the Telegram approval webhook
(`api/telegram_webhook.py`). The nightly cron lives on GitHub Actions.

## 4. Point Telegram at the webhook

After `vercel deploy --prod` prints your domain:

```powershell
$url = "https://<your-vercel-domain>/api/telegram_webhook"
curl "https://api.telegram.org/bot$env:TELEGRAM_BOT_TOKEN/setWebhook?url=$url"
```

## 5. GitHub Secrets for the nightly cron

Repo → Settings → Secrets and variables → Actions → New repository secret:

* `GROQ_API_KEY`
* `HUNTER_API_KEY`
* `DATABASE_URL`
* `SUPABASE_URL`
* `SUPABASE_ANON_KEY`
* `TELEGRAM_BOT_TOKEN`
* `TELEGRAM_CHAT_ID`

(Reddit secrets intentionally skipped — the workflow pins
`REDDIT_CLIENT_ID=TODO_karma_gate` and the scraper + dispatch skip cleanly.)

## 6. Local smoke test

```powershell
cd bot1-intent-hijacker
poetry install
poetry run playwright install chromium
poetry run python -c "import workers.nightly_run"   # import check only
$env:BOT1_DRY_RUN = "true"
poetry run python -m workers.nightly_run
```

`BOT1_DRY_RUN=true` short-circuits every actual send/submit and only logs
what it *would* do.
