"""Polymarket market discovery via the public Gamma API (read-only, no credentials).

Maps Polymarket's raw Gamma market JSON into the framework's venue-neutral `Market`,
and constructs the UTC-aligned interval slugs for up/down crypto markets
(e.g. `btc-updown-5m-1779449400`).
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime

from oraclebook.core.models import Market

GAMMA = "https://gamma-api.polymarket.com"


def interval_start(minutes: int, ts: float) -> int:
    """UTC-aligned start of the interval containing `ts` (5m/15m boundaries are whole)."""
    sec = minutes * 60
    return int(ts) - (int(ts) % sec)


def updown_slug(asset: str, minutes: int, ts: float) -> str:
    """Gamma slug for an asset's up/down interval market, e.g. 'btc-updown-5m-1779449400'."""
    return f"{asset.lower()}-updown-{minutes}m-{interval_start(minutes, ts)}"


def _get(url: str, timeout: float = 15.0):
    req = urllib.request.Request(url, headers={"User-Agent": "oraclebook"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _to_market(m: dict) -> Market | None:
    cond = m.get("conditionId")
    if not cond:
        return None
    toks = m.get("clobTokenIds")
    outs = m.get("outcomes")
    if isinstance(toks, str):
        toks = json.loads(toks)
    if isinstance(outs, str):
        outs = json.loads(outs)
    end_ts: float | None = None
    if m.get("endDate"):
        try:
            end_ts = datetime.fromisoformat(m["endDate"].replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return Market(
        venue="polymarket",
        market_id=str(m.get("id") or cond),
        condition_id=cond,
        token_ids=tuple(toks or ()),
        outcomes=tuple(outs or ()),
        tick_size=str(m.get("orderPriceMinTickSize", "0.01")),
        min_order_size=float(m.get("orderMinSize") or 0.0),
        end_ts=end_ts,
        fee_taker_only=True,
        meta={"slug": m.get("slug", "")},
    )


def market_by_slug(slug: str) -> Market | None:
    """Fetch a single market by Gamma slug (tries open, then closed)."""
    for suffix in ("", "&closed=true"):
        data = _get(f"{GAMMA}/markets?slug={slug}{suffix}")
        if isinstance(data, list) and data:
            return _to_market(data[0])
    return None


def discover(assets: list[str], intervals: list[int], now: float | None = None) -> list[Market]:
    """Discover the current and next up/down markets for the given assets and intervals."""
    now = now or time.time()
    out: list[Market] = []
    seen: set[str] = set()
    for asset in assets:
        for minutes in intervals:
            for ts in (now, now + minutes * 60):  # current + next interval
                slug = updown_slug(asset, minutes, ts)
                if slug in seen:
                    continue
                seen.add(slug)
                market = market_by_slug(slug)
                if market is not None:
                    out.append(market)
    return out
