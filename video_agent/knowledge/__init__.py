"""Editing-technique knowledge with provenance (techniques.yaml; collected in docs/learning-sources.md).

Code takes its documented defaults from here instead of magic numbers, so every value can be traced to
the page it came from. `status` says how far to trust it: verified (page read), snippet (search result
only), derived, heuristic (a tunable default, never a hard rule) or uncertain.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

INDEX = Path(__file__).with_name("techniques.yaml")


@lru_cache
def techniques() -> dict[str, dict]:
    return {t["id"]: t for t in yaml.safe_load(INDEX.read_text())}


def technique(tid: str) -> dict:
    try:
        return techniques()[tid]
    except KeyError:
        raise KeyError(f"No technique '{tid}' in {INDEX.name}") from None


def param(tid: str, name: str):
    """A parameter's default (or fixed value) as documented for a technique."""
    p = technique(tid).get("parameters", {})[name]
    if isinstance(p, dict):
        return p["default"] if "default" in p else p["value"]
    return p


def sources(tid: str) -> list[str]:
    return [s["url"] for s in technique(tid).get("sources", [])]


def brief(topics: list[str] | None = None) -> str:
    """Plain-text takeaways for a prompt (the director or the feedback parser), one line per technique."""
    rows = [t for t in techniques().values() if topics is None or t.get("topic") in topics]
    return "\n".join(f"- {t['id']}: {' '.join(str(t.get('takeaway', '')).split())}" for t in rows)
