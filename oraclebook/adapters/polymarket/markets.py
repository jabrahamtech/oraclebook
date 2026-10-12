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
from decimal import Decimal, InvalidOperation

import structlog

from oraclebook.core.models import Market

logger = structlog.get_logger()

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


def _json_field(value):
    """Gamma encodes some list/object fields as JSON strings."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _tick_str(value) -> str:
    try:
        return format(Decimal(str(value)).normalize(), "f")
    except InvalidOperation:
        return "0.01"


def _fee_meta(m: dict) -> tuple[dict, bool, bool]:
    """(fee meta, fee_verified, fee_taker_only) from Gamma's feesEnabled + feeSchedule."""
    if m.get("feesEnabled") is False:
        return {"fee_rate": 0.0, "fee_exponent": 1.0}, True, True
    schedule = _json_field(m.get("feeSchedule"))
    if not isinstance(schedule, dict):
        return {}, False, True
    try:
        meta = {
            "fee_rate": float(schedule["rate"]),
            "fee_exponent": float(schedule["exponent"]),
            "fee_rebate_rate": float(schedule.get("rebateRate") or 0.0),
        }
    except (KeyError, TypeError, ValueError):
        logger.warning("polymarket_markets.bad_fee_schedule", market=m.get("id"), schedule=schedule)
        return {}, False, True
    return meta, True, bool(schedule.get("takerOnly", True))


def _to_market(m: dict) -> Market | None:
    cond = m.get("conditionId")
    if not cond:
        return None
    toks = list(_json_field(m.get("clobTokenIds")) or ())
    outs = list(_json_field(m.get("outcomes")) or ())
    if outs and len(outs) != len(toks):
        # outcomes[i] labels clobTokenIds[i]; without a 1:1 pairing the labels are unusable.
        logger.warning("polymarket_markets.outcome_token_mismatch", market=m.get("id"),
                       outcomes=outs, tokens=toks)
        return None
    end_ts: float | None = None
    if m.get("endDate"):
        try:
            end_ts = datetime.fromisoformat(m["endDate"]).timestamp()
        except ValueError:
            pass
    fee_meta, fee_verified, fee_taker_only = _fee_meta(m)
    meta = {
        "slug": m.get("slug", ""),
        "token_by_outcome": dict(zip(outs, toks, strict=True)) if outs else {},
        **fee_meta,
    }
    if m.get("negRisk") is not None:
        meta["neg_risk"] = bool(m["negRisk"])
    return Market(
        venue="polymarket",
        market_id=str(m.get("id") or cond),
        condition_id=cond,
        token_ids=tuple(toks),
        outcomes=tuple(outs),
        tick_size=_tick_str(m.get("orderPriceMinTickSize") or "0.01"),
        min_order_size=float(m.get("orderMinSize") or 0.0),
        end_ts=end_ts,
        fee_taker_only=fee_taker_only,
        fee_verified=fee_verified,
        meta=meta,
    )


def token_for_outcome(market: Market, outcome: str) -> str | None:
    """The token id for an outcome label (case-insensitive), e.g. "Up" or "No". Token order
    follows Gamma's `outcomes`; never assume the first token is YES/Up."""
    for label, token in market.meta.get("token_by_outcome", {}).items():
        if label.lower() == outcome.lower():
            return token
    return None


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
