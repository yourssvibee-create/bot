# `shared/llm` — multi-provider LLM facade

Four providers (Groq → Gemini → NVIDIA → OpenRouter) behind one `draft()` /
`score_json()` call. The router walks `LLM_PROVIDER_ORDER`, retries on
429 / 5xx / empty-output / JSON-validation failure, and raises
`LLMExhaustedError` only when every provider has failed.

```python
from shared.llm import draft, score_json, LLMExhaustedError
text = await draft("You are a helpful commenter.", "Say hi in 10 words.")
```

Adding a fifth provider is a one-file add under `providers/` plus a line in
`router.PROVIDER_REGISTRY`.

## API keys are per-bot, never shared.

The code in this package is reusable across every bot in this monorepo —
but the keys that back it must stay **isolated per unit**.

* Each bot folder has its own `.env` with its own `GROQ_API_KEY`,
  `GEMINI_API_KEY`, `NVIDIA_API_KEY`, `OPENROUTER_API_KEY`, each
  registered under that bot's own Gmail (and, for paid tiers, its own
  billing).
* `shared.config.load_env()` enforces this: it looks for the `.env`
  **in the bot folder only** (same directory as `pyproject.toml`). It does
  not walk up to the repo root, and it refuses to load if a stray `.env`
  exists at the repo root.
* `shared.config.require_bot_id()` further enforces that `BOT_ID=<botN>`
  is set, with the value whitelisted in `KNOWN_BOT_IDS`. The router tags
  every log line with that `bot_id`; if a future bot accidentally loads
  Unit 1's keys, the logs will scream.

### Current ownership (as of 2026-11)

| Unit | Bots | Gmails holding the LLM keys |
|---|---|---|
| **Unit 1** | bot1, bot2 | `yourssvibee@gmail.com`, `ominvoo@gmail.com` |
| Unit 2+ | TBD | **register fresh keys under that unit's own Gmails — never copy Unit 1's** |

### Rules for the next bot

1. `cp bot1-intent-hijacker/.env.example botN/.env`
2. Change `BOT_ID=bot1` → `BOT_ID=botN`.
3. Create fresh Groq / Gemini / NVIDIA / OpenRouter accounts under the
   unit's own Gmail and paste those keys into `botN/.env`.
4. Add `botN` to `shared.config.KNOWN_BOT_IDS` if not present.
5. Never commit the `.env`. Never put a `.env` at the repo root.
