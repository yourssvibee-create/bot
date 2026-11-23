"""Hunter.io domain -> owner email enrichment. Best-effort, fail-soft."""
from __future__ import annotations

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

import sys
from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.logging import get_logger

from ..config import settings
from ..types import Business

log = get_logger("bot7.enrich.hunter")

_API = "https://api.hunter.io/v2/domain-search"

# Decision-maker titles we prefer, in order.
_PRIORITY = (
    "owner",
    "founder",
    "ceo",
    "president",
    "managing partner",
    "principal",
    "partner",
    "director",
    "manager",
)


def _score_title(title: str) -> int:
    t = (title or "").lower()
    for i, p in enumerate(_PRIORITY):
        if p in t:
            return len(_PRIORITY) - i
    return 0


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1.5, min=1, max=10),
    reraise=True,
)
def _domain_search(domain: str, api_key: str) -> dict:
    r = httpx.get(
        _API,
        params={"domain": domain, "api_key": api_key, "limit": 5},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def enrich_one(b: Business) -> Business:
    """Mutate+return the business with best-match email. Fail-soft."""
    if not b.domain or not settings.hunter_api_key:
        return b
    try:
        data = _domain_search(b.domain, settings.hunter_api_key)
    except Exception as e:
        log.warning("hunter.fail domain=%s err=%s", b.domain, e)
        return b

    emails = (data.get("data") or {}).get("emails") or []
    if not emails:
        return b

    emails.sort(key=lambda e: _score_title(e.get("position", "")), reverse=True)
    best = emails[0]
    b.owner_email = best.get("value", "") or ""
    b.owner_first_name = best.get("first_name", "") or ""
    b.owner_last_name = best.get("last_name", "") or ""
    b.owner_title = best.get("position", "") or ""
    conf = best.get("confidence")
    b.email_confidence = int(conf) if isinstance(conf, (int, float)) else None
    return b


def enrich_batch(businesses: list[Business], cap: int) -> list[Business]:
    """Enrich up to `cap` businesses (prioritize those with a domain)."""
    with_domain = [b for b in businesses if b.domain]
    without = [b for b in businesses if not b.domain]
    target = with_domain[:cap]
    log.info("hunter.batch total=%s with_domain=%s enriching=%s", len(businesses), len(with_domain), len(target))
    for b in target:
        enrich_one(b)
    return target + with_domain[cap:] + without
