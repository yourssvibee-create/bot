"""Bot 1 nightly orchestrator.

Order: scrape -> score -> draft -> approve -> dispatch -> segment/handoff.
Idempotent: unique (source, source_id) on signals, unique (signal_id) on leads,
unique (lead_id) on handoff_to_leadgen.
Resumable: any step that fails is logged and the run continues.
"""
from __future__ import annotations

# Bootstrap: make `shared/` importable when run from inside bot1-intent-hijacker/.
import sys
from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import asyncio
from typing import Any

from shared.db import fetch_one, upsert
from shared.logging import configure, get_logger

from src.config import settings
from src.dispatch import contact_forms, social_reply
from src.dispatch.base import record
from src.drafter import draft_reply
from src.handoff_to_leadgen import queue_for_leadgen
from src.scoring import score
from src.scrapers import maps_feed, quora, reddit_scraper, trustpilot, x_scraper
from src.segmenter import classify
from src.telegram_approve import enqueue, wait_for_decision

configure()
log = get_logger("bot1.nightly")


async def _run_scrapers() -> list[dict]:
    log.info("scrape_begin")
    signals: list[dict] = []
    tp = await trustpilot.scrape()
    signals.extend(tp)
    qa = await quora.scrape()
    signals.extend(qa)
    xs = await x_scraper.scrape()
    signals.extend(xs)
    rs = reddit_scraper.scrape()          # may be [] when karma-gated
    signals.extend(rs)
    mf = maps_feed.scrape()               # may be [] when bot7 is empty
    signals.extend(mf)
    log.info("scrape_done", extra={"count": len(signals)})
    return signals


def _persist_signal(sig: dict) -> str | None:
    row = upsert(
        "signals",
        {
            "source": sig["source"],
            "source_id": sig["source_id"],
            "url": sig.get("url"),
            "author": sig.get("author"),
            "title": sig.get("title"),
            "body": sig.get("body"),
            "raw": _json(sig.get("raw") or {}),
        },
        conflict_cols=["source", "source_id"],
        update_cols=["url", "author", "title", "body", "raw"],
    )
    return str(row["id"]) if row else None


def _json(o: Any) -> str:
    import json
    return json.dumps(o, default=str, ensure_ascii=False)


async def _score_and_persist(sig_id: str, sig: dict) -> dict | None:
    try:
        s = await score(sig)
    except Exception as e:
        log.warning("score_fail", extra={"sig_id": sig_id, "err": str(e)})
        record("score", target=sig.get("url"), channel=None,
               status="fail", detail={"err": str(e)})
        return None

    classified = classify(sig, s.intent_score, s.segment)
    row = upsert(
        "leads",
        {
            "signal_id": sig_id,
            "intent_score": s.intent_score,
            "reason": s.reason,
            "segment": classified["segment"],
            "website": classified["website"],
        },
        conflict_cols=["signal_id"],
        update_cols=["intent_score", "reason", "segment", "website"],
    )
    if not row:
        return None
    return {
        "lead_id": str(row["id"]),
        "signal": sig,
        "score": s,
        "classified": classified,
    }


async def _handle_lead(lead: dict) -> None:
    cfg = settings()
    sig = lead["signal"]
    classified = lead["classified"]
    segment = classified["segment"]
    channel = classified["channel"]
    lead_id = lead["lead_id"]
    intent = lead["score"].intent_score

    if segment == "drop" or intent < cfg.min_intent_score and segment != "email_only_fallback":
        record("skip", target=sig.get("url"), channel=channel,
               status="skip", detail={"reason": "below_threshold", "intent": intent,
                                      "segment": segment})
        return

    if segment == "email_only_fallback":
        if classified["website"]:
            queue_for_leadgen(lead_id, classified["website"],
                              signal=sig, intent_reason=lead["score"].reason)
            record("handoff", target=classified["website"], channel="email",
                   status="ok", detail={"lead_id": lead_id})
        else:
            record("handoff", target=None, channel="email",
                   status="skip", detail={"reason": "no_website", "lead_id": lead_id})
        return

    if channel is None:
        record("skip", target=sig.get("url"), channel=None,
               status="skip", detail={"reason": "no_channel", "lead_id": lead_id})
        return

    # Draft
    try:
        text = await draft_reply(sig, channel)
    except Exception as e:
        log.warning("draft_fail", extra={"err": str(e), "lead_id": lead_id})
        record("draft", target=sig.get("url"), channel=channel,
               status="fail", detail={"err": str(e)})
        return
    if not text:
        record("draft", target=sig.get("url"), channel=channel,
               status="skip", detail={"reason": "model_skip"})
        return

    # Telegram approval
    drafted_id = enqueue(lead_id, channel, text)
    record("approve", target=sig.get("url"), channel=channel,
           status="ok", detail={"drafted_id": drafted_id})

    decision = await asyncio.to_thread(wait_for_decision, drafted_id)
    if decision != "approved":
        record("approve", target=sig.get("url"), channel=channel,
               status="skip", detail={"decision": decision, "drafted_id": drafted_id})
        return

    # Dispatch
    if channel == "contact_form":
        result = await contact_forms.submit(classified["website"], text)
    else:
        target_url = sig.get("url") or ""
        result = await social_reply.post(channel, target_url, text)

    outcome = result["status"]
    from shared.db import execute
    execute(
        "insert into replies_sent (drafted_id, channel, external_id, outcome) "
        "values (%s, %s, %s, %s)",
        (drafted_id, channel, (result.get("detail") or {}).get("external_id"), outcome),
    )
    if outcome == "ok":
        execute("update replies_drafted set status='sent', decided_at=now() where id=%s",
                (drafted_id,))


async def _main_async() -> None:
    cfg = settings()
    log.info("bot1_begin", extra={"dry_run": cfg.dry_run,
                                   "min_intent": cfg.min_intent_score})

    signals = await _run_scrapers()
    persisted: list[tuple[str, dict]] = []
    for sig in signals:
        try:
            sig_id = _persist_signal(sig)
        except Exception as e:
            log.warning("signal_persist_fail",
                        extra={"err": str(e), "url": sig.get("url")})
            continue
        if sig_id:
            persisted.append((sig_id, sig))
    log.info("signals_persisted", extra={"count": len(persisted)})

    # Only score signals that don't already have a lead (resumable).
    leads: list[dict] = []
    for sig_id, sig in persisted:
        already = fetch_one("select 1 from leads where signal_id=%s", (sig_id,))
        if already:
            continue
        scored = await _score_and_persist(sig_id, sig)
        if scored:
            leads.append(scored)
    log.info("scored", extra={"count": len(leads)})

    # Dispatch pass — one lead at a time so a stuck approval doesn't block others forever.
    # (The 12h timeout bounds each.)
    for lead in leads:
        try:
            await _handle_lead(lead)
        except Exception as e:
            log.warning("lead_handle_fail",
                        extra={"err": str(e), "lead_id": lead["lead_id"]})
            record("error", target=lead["signal"].get("url"),
                   channel=lead["classified"]["channel"],
                   status="fail", detail={"err": str(e), "lead_id": lead["lead_id"]})

    log.info("bot1_end", extra={"signals": len(persisted), "leads": len(leads)})


def main() -> None:
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()
