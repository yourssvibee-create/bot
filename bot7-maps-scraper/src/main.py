"""Bot 7 orchestrator. Scrape -> enrich -> CSV -> Supabase -> digest.

Fail-soft: a single bad segment never crashes the run; it's recorded in the
PhaseTracker and the digest.
"""
from __future__ import annotations

import asyncio
import time
import traceback
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import sys
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.logging import configure, get_logger

from .config import settings
from .enrich.hunter import enrich_batch
from .listing.publish import PackSpec, list_to_channels, populate_packs
from .notify.telegram import send_digest
from .output.csv_writer import write_segment_csv
from .scrapers.google_maps import scrape_segment
from .segments import build_matrix, load_metros, load_verticals
from .storage.supabase_client import log_run, upsert_businesses
from .types import Segment


def _csv_set(s: str) -> set[str]:
    return {x.strip() for x in s.split(",") if x.strip()}


def _apply_filters() -> list[Segment]:
    """Build the segment matrix, then optionally narrow by env filters.

    BOT7_METROS_FILTER / BOT7_VERTICALS_FILTER are comma-separated slugs.
    An empty filter means "all". Unknown slugs are silently skipped.
    """
    metros = load_metros()
    verticals = load_verticals()
    mf = _csv_set(settings.metros_filter)
    vf = _csv_set(settings.verticals_filter)
    if mf:
        metros = [m for m in metros if m.slug in mf]
    if vf:
        verticals = [v for v in verticals if v.slug in vf]
    return build_matrix(metros, verticals)

configure()
log = get_logger("bot7.main")


@dataclass
class SegmentResult:
    segment: Segment
    records: int
    emails: int
    csv_path: Optional[Path]
    rows: list                      # list[Business], kept for the listing phase
    error: Optional[str]


async def _run_segment(seg: Segment) -> SegmentResult:
    try:
        rows = await scrape_segment(seg, cap=settings.scrape_cap_per_segment)
    except Exception as e:
        traceback.print_exc()
        return SegmentResult(seg, 0, 0, None, [], f"scrape: {e}")

    if not rows:
        return SegmentResult(seg, 0, 0, None, [], "scrape: 0 records")

    try:
        rows = await enrich_batch(rows, cap=settings.hunter_cap_per_segment)
    except Exception as e:
        log.warning("bot7.enrich.fail seg=%s err=%s", seg.slug, e)

    emails = sum(1 for b in rows if b.owner_email)

    try:
        csv_path = write_segment_csv(seg, rows)
    except Exception as e:
        return SegmentResult(seg, len(rows), emails, None, [], f"csv: {e}")

    try:
        upsert_businesses(seg, rows)
    except Exception as e:
        log.warning("bot7.db.fail seg=%s err=%s", seg.slug, e)

    return SegmentResult(seg, len(rows), emails, csv_path, rows, None)


async def run(segments: Iterable[Segment] | None = None) -> int:
    start = time.time()
    started_at = datetime.now(timezone.utc)

    segs = list(segments) if segments is not None else _apply_filters()
    log.info(
        "bot7.run.start segments=%s dry_run=%s metros_filter=%r verticals_filter=%r",
        len(segs), settings.dry_run, settings.metros_filter, settings.verticals_filter,
    )

    results: list[SegmentResult] = []
    for i, seg in enumerate(segs):
        log.info("bot7.run.seg %s/%s %s", i + 1, len(segs), seg.slug)
        res = await _run_segment(seg)
        results.append(res)
        if i < len(segs) - 1 and settings.inter_segment_sleep_s > 0:
            await asyncio.sleep(settings.inter_segment_sleep_s)

    ok = [r for r in results if r.error is None]
    failures = [(r.segment.slug, r.error or "") for r in results if r.error]
    total_records = sum(r.records for r in results)
    total_emails = sum(r.emails for r in results)

    # ---- Phase 2: populate packs + push to channels ----
    channel_counts: dict[str, int] = {}
    try:
        packs = [
            PackSpec(segment=r.segment, rows=r.rows, csv_path=r.csv_path)
            for r in ok if r.csv_path is not None
        ]
        ready = populate_packs(packs)
        log.info("bot7.phase.packs ready=%s total=%s", ready, len(packs))
        if ready:
            channel_counts = await list_to_channels(packs)
    except Exception as e:
        log.warning("bot7.phase.listing.fail err=%s", e)
        failures.append(("<listing>", str(e)[:200]))

    duration = int(time.time() - start)
    finished_at = datetime.now(timezone.utc)

    try:
        log_run(
            started_at=started_at,
            finished_at=finished_at,
            segments_total=len(segs),
            segments_ok=len(ok),
            records_total=total_records,
            emails_found=total_emails,
            status="ok" if not failures else ("partial" if ok else "fail"),
            notes=f"dry_run={settings.dry_run}; failures={len(failures)}",
        )
    except Exception as e:
        log.warning("bot7.log_run.fail err=%s", e)

    send_digest(
        run_date=date.today().isoformat(),
        segments_total=len(segs),
        segments_ok=len(ok),
        records_total=total_records,
        emails_found=total_emails,
        duration_s=duration,
        failures=failures,
        dry_run=settings.dry_run,
        channel_counts=channel_counts or None,
    )

    log.info(
        "bot7.run.done segments=%s/%s records=%s emails=%s dur=%ss",
        len(ok), len(segs), total_records, total_emails, duration,
    )
    return 0 if ok else 1


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
