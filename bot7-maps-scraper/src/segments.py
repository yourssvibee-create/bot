"""Load metros/verticals from YAML and build the 192-segment matrix."""
from __future__ import annotations

from typing import Iterable

import yaml

from .config import CONFIG_DIR
from .types import Metro, Segment, Vertical


def load_metros() -> list[Metro]:
    data = yaml.safe_load((CONFIG_DIR / "metros.yaml").read_text(encoding="utf-8"))
    return [Metro(**m) for m in data["metros"]]


def load_verticals() -> list[Vertical]:
    data = yaml.safe_load((CONFIG_DIR / "verticals.yaml").read_text(encoding="utf-8"))
    return [
        Vertical(
            slug=v["slug"],
            name=v["name"],
            query=v["query"],
            chain_exclusions=[s.lower() for s in v.get("chain_exclusions", [])],
        )
        for v in data["verticals"]
    ]


def load_pricing() -> dict:
    """Return full pricing table:
      { "tiers": {1: {one_time, monthly, metro_bundle}, ...},
        "everything_bundle": int }
    """
    data = yaml.safe_load((CONFIG_DIR / "pricing.yaml").read_text(encoding="utf-8"))
    tiers = {int(k): dict(v) for k, v in data["tiers"].items()}
    return {"tiers": tiers, "everything_bundle": int(data.get("everything_bundle", 0))}


def price_for(tier: int, kind: str = "one_time") -> int:
    """Lookup USD price for (tier, kind). kind in {one_time, monthly, metro_bundle}."""
    return int(load_pricing()["tiers"][int(tier)][kind])


def build_matrix(
    metros: Iterable[Metro] | None = None,
    verticals: Iterable[Vertical] | None = None,
) -> list[Segment]:
    ms = list(metros) if metros is not None else load_metros()
    vs = list(verticals) if verticals is not None else load_verticals()
    return [Segment(metro=m, vertical=v) for m in ms for v in vs]


def is_chain(name: str, exclusions: list[str]) -> bool:
    n = name.lower()
    return any(x in n for x in exclusions)
