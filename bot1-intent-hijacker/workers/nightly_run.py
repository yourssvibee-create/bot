"""Bot 1 nightly orchestrator.

Fail-soft diagnostic mode: each major phase is wrapped so one failure doesn't
stop the run. At the end, prints a phase summary table and exits 1 if any
phase failed, 0 if all succeeded.

Phases: scrape_trustpilot, scrape_quora, scrape_nitter, scrape_reddit, score,
draft, telegram_queue, dispatch_contact_forms, dispatch_social_reply,
handoff_to_leadgen.

BOT1_DRY_RUN=true short-circuits dispatch/handoff/telegram phases to log-only.
"""
from __future__ import annotations

# Bootstrap: make `shared/` importable when run from inside bot1-intent-hijacker/.
import sys
from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import asyncio
import traceback
from typing import Any, Callable

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


class PhaseTracker:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []  # (name, status, note)

    def ok(self, name: str, note: str = "") -> None:
        self.rows.append((name, "ok", note))

    def fail(self, name: str, err: BaseException) -> None:
        traceback.print_exc()
        msg = str(err).splitlines()[0] if str(err) else type(err).__name__
        self.rows.append((name, "fail", msg[:200]))

    def any_failed(self) -> bool:
        return any(s == "fail" for _, s, _ in self.rows)

    def render(self) -> str:
        if not self.rows:
            return "(no phases recorded)"
        w = max(len(n) for n, _, _ in self.rows)
        out = ["", "=" * 78, "PHASE SUMMARY", "=" * 78]
        for name, status, note in self.rows:
            marker = "OK  " if status == "ok" else "FAIL"
            out.append(f"  {marker}  {name.ljust(w)}  {note}")
        out.append("=" * 78)
        return "\n".join(out)


def _json(o: Any) -> str:
    import json
    return json.dumps(o, default=str, ensure_ascii=False)


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


async def _run_scrape_phase(
    t: PhaseTracker, name: str, scraper: Callable, is_async: bool
) -> list[dict]:
    try:
        result = await scraper() if is_async else scraper()
        result = list(result or [])
        t.ok(name, f"{len(result)} signals")
        return result
    except Exception as e:
        t.fail(name, e)
        return []


async def _score_one(sig_id: str, sig: dict) -> dict | None:
    s = await score(sig)
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


async def _main_async() -> int:
    cfg = settings()
    log.info("bot1_begin",
             extra={"dry_run": cfg.dry_run, "min_intent": cfg.min_intent_score})
    t = PhaseTracker()

    # --- Scrape phases ---
    signals: list[dict] = []
    signals += await _run_scrape_phase(t, "scrape_trustpilot", trustpilot.scrape, True)
    signals += await _run_scrape_phase(t, "scrape_quora", quora.scrape, True)
    signals += await _run_scrape_phase(t, "scrape_nitter", x_scraper.scrape, True)
    signals += await _run_scrape_phase(t, "scrape_reddit", reddit_scraper.scrape, False)
    # maps_feed is a handoff from bot7; may be empty and is not tracked as a top phase.
    try:
        signals += list(maps_feed.scrape() or [])
    except Exception as e:
        log.warning("maps_feed_fail", extra={"err": str(e)})

    # --- Score phase (persist signals + score) ---
    leads: list[dict] = []
    persisted: list[tuple[str, dict]] = []
    score_errors = 0
    rate_limited = 0
    try:
        for sig in signals:
            try:
                sig_id = _persist_signal(sig)
                if sig_id:
                    persisted.append((sig_id, sig))
            except Exception as e:
                log.warning("signal_persist_fail",
                            extra={"err": str(e), "url": sig.get("url")})
        for sig_id, sig in persisted:
            if fetch_one("select 1 from leads where signal_id=%s", (sig_id,)):
                continue
            try:
                scored = await _score_one(sig_id, sig)
                if scored:
                    leads.append(scored)
            except Exception as e:
                msg = str(e).lower()
                is_rl = ("rate limit" in msg or "rate_limit" in msg
                         or "429" in msg or "providers exhausted" in msg)
                if is_rl:
                    rate_limited += 1
                else:
                    score_errors += 1
                log.warning("score_fail",
                            extra={"err": str(e), "sig_id": sig_id,
                                   "rate_limited": is_rl})
                record("score", target=sig.get("url"), channel=None,
                       status="skip" if is_rl else "fail",
                       detail={"err": str(e), "rate_limited": is_rl})
            # Pace score calls to stay under Groq TPM/RPM caps.
            await asyncio.sleep(0.6)
        note = f"{len(persisted)} persisted, {len(leads)} new leads"
        if rate_limited:
            note += f", {rate_limited} rate-limited (skipped)"
        if score_errors:
            t.fail("score", RuntimeError(
                f"{score_errors}/{len(persisted)} scores failed ({note})"))
        else:
            t.ok("score", note)
    except Exception as e:
        t.fail("score", e)

    # Partition leads for subsequent phases.
    eligible: list[dict] = []     # need draft + approval + dispatch
    handoff_leads: list[dict] = []  # email_only_fallback -> leadgen
    for lead in leads:
        seg = lead["classified"]["segment"]
        ch = lead["classified"]["channel"]
        intent = lead["score"].intent_score
        if seg == "drop":
            continue
        if seg == "email_only_fallback":
            handoff_leads.append(lead)
            continue
        if intent < cfg.min_intent_score or ch is None:
            continue
        eligible.append(lead)

    # --- Draft phase ---
    drafted: list[tuple[dict, str]] = []
    draft_errors = 0
    for lead in eligible:
        sig = lead["signal"]
        channel = lead["classified"]["channel"]
        sig_for_draft = {
            **sig,
            "intent_score": lead["score"].intent_score,
            "pain_point": lead["score"].reason,
        }
        try:
            text = await draft_reply(sig_for_draft, channel)
        except Exception as e:
            draft_errors += 1
            log.warning("draft_fail",
                        extra={"err": str(e), "lead_id": lead["lead_id"]})
            record("draft", target=sig.get("url"), channel=channel,
                   status="fail", detail={"err": str(e)})
            continue
        if text:
            drafted.append((lead, text))
    if draft_errors:
        t.fail("draft", RuntimeError(
            f"{draft_errors}/{len(eligible)} drafts failed"))
    else:
        t.ok("draft",
             f"{len(drafted)} drafted / {len(eligible)} eligible")

    # --- Telegram queue phase ---
    approved: list[tuple[dict, str, str]] = []  # (lead, text, drafted_id)
    if cfg.dry_run:
        for lead, text in drafted:
            log.info("telegram_queue_would_enqueue",
                     extra={"lead_id": lead["lead_id"], "chars": len(text)})
        t.ok("telegram_queue",
             f"DRY_RUN: would enqueue {len(drafted)}")
    else:
        tq_errors = 0
        for lead, text in drafted:
            try:
                did = enqueue(lead["lead_id"], lead["classified"]["channel"], text)
                record("approve", target=lead["signal"].get("url"),
                       channel=lead["classified"]["channel"],
                       status="ok", detail={"drafted_id": did})
                decision = await asyncio.to_thread(wait_for_decision, did)
                if decision == "approved":
                    approved.append((lead, text, did))
                else:
                    record("approve", target=lead["signal"].get("url"),
                           channel=lead["classified"]["channel"],
                           status="skip",
                           detail={"decision": decision, "drafted_id": did})
            except Exception as e:
                tq_errors += 1
                log.warning("telegram_queue_fail",
                            extra={"err": str(e), "lead_id": lead["lead_id"]})
        if tq_errors:
            t.fail("telegram_queue", RuntimeError(
                f"{tq_errors}/{len(drafted)} queue ops failed"))
        else:
            t.ok("telegram_queue",
                 f"{len(approved)}/{len(drafted)} approved")

    # --- Dispatch: contact_forms ---
    if cfg.dry_run:
        n_cf = sum(1 for l, _ in drafted
                   if l["classified"]["channel"] == "contact_form")
        log.info("dispatch_contact_forms_would_dispatch", extra={"count": n_cf})
        t.ok("dispatch_contact_forms",
             f"DRY_RUN: would dispatch {n_cf}")
    else:
        cf_items = [(l, txt) for (l, txt, _) in approved
                    if l["classified"]["channel"] == "contact_form"]
        cf_errors = 0
        for lead, text in cf_items:
            try:
                await contact_forms.submit(lead["classified"]["website"], text)
            except Exception as e:
                cf_errors += 1
                log.warning("dispatch_contact_forms_fail",
                            extra={"err": str(e), "lead_id": lead["lead_id"]})
        if cf_errors:
            t.fail("dispatch_contact_forms", RuntimeError(
                f"{cf_errors}/{len(cf_items)} failed"))
        else:
            t.ok("dispatch_contact_forms",
                 f"{len(cf_items)} dispatched")

    # --- Dispatch: social_reply ---
    if cfg.dry_run:
        n_sr = sum(1 for l, _ in drafted
                   if l["classified"]["channel"] != "contact_form")
        log.info("dispatch_social_reply_would_dispatch", extra={"count": n_sr})
        t.ok("dispatch_social_reply",
             f"DRY_RUN: would dispatch {n_sr}")
    else:
        sr_items = [(l, txt) for (l, txt, _) in approved
                    if l["classified"]["channel"] != "contact_form"]
        sr_errors = 0
        for lead, text in sr_items:
            try:
                target_url = lead["signal"].get("url") or ""
                await social_reply.post(lead["classified"]["channel"],
                                        target_url, text)
            except Exception as e:
                sr_errors += 1
                log.warning("dispatch_social_reply_fail",
                            extra={"err": str(e), "lead_id": lead["lead_id"]})
        if sr_errors:
            t.fail("dispatch_social_reply", RuntimeError(
                f"{sr_errors}/{len(sr_items)} failed"))
        else:
            t.ok("dispatch_social_reply",
                 f"{len(sr_items)} dispatched")

    # --- Handoff to leadgen ---
    if cfg.dry_run:
        log.info("handoff_to_leadgen_would_handoff",
                 extra={"count": len(handoff_leads)})
        t.ok("handoff_to_leadgen",
             f"DRY_RUN: would handoff {len(handoff_leads)}")
    else:
        h_errors = 0
        n_h = 0
        for lead in handoff_leads:
            website = lead["classified"]["website"]
            if not website:
                continue
            try:
                queue_for_leadgen(lead["lead_id"], website,
                                  signal=lead["signal"],
                                  intent_reason=lead["score"].reason)
                n_h += 1
            except Exception as e:
                h_errors += 1
                log.warning("handoff_fail",
                            extra={"err": str(e), "lead_id": lead["lead_id"]})
        if h_errors:
            t.fail("handoff_to_leadgen", RuntimeError(
                f"{h_errors}/{len(handoff_leads)} failed"))
        else:
            t.ok("handoff_to_leadgen", f"{n_h} handed off")

    # --- Summary ---
    print(t.render(), flush=True)
    log.info("bot1_end", extra={
        "signals": len(signals),
        "leads": len(leads),
        "any_failed": t.any_failed(),
    })
    return 1 if t.any_failed() else 0


def main() -> None:
    rc = asyncio.run(_main_async())
    sys.exit(rc)


if __name__ == "__main__":
    main()
