"""Shared record types."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class Metro:
    slug: str
    name: str
    query_suffix: str
    tier: int


@dataclass
class Vertical:
    slug: str
    name: str
    query: str
    chain_exclusions: list[str] = field(default_factory=list)


@dataclass
class Business:
    name: str
    street_address: str = ""   # from detail-pane aria-label (micro-click)
    tagline: str = ""          # short description shown in the feed card
    phone: str = ""
    website: str = ""
    category: str = ""
    rating: Optional[float] = None
    review_count: Optional[int] = None
    google_maps_url: str = ""
    # Enriched later:
    domain: str = ""
    owner_email: str = ""
    owner_first_name: str = ""
    owner_last_name: str = ""
    owner_title: str = ""
    email_confidence: Optional[int] = None
    # When the row was scraped (ISO-8601, UTC). Populated by the scraper.
    scraped_at: str = ""

    def to_row(self) -> dict:
        return asdict(self)


@dataclass
class Segment:
    metro: Metro
    vertical: Vertical

    @property
    def slug(self) -> str:
        return f"{self.metro.slug}__{self.vertical.slug}"

    @property
    def query(self) -> str:
        return f"{self.vertical.query} in {self.metro.query_suffix}"
