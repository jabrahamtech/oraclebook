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
from zoneinfo import ZoneInfo

import structlog

from oraclebook.adapters.polymarket.fees import CRYPTO_FEE_EXPONENT, CRYPTO_FEE_RATE
from oraclebook.core.models import Market

logger = structlog.get_logger()

GAMMA = "https://gamma-api.polymarket.com"

_ET = ZoneInfo("America/New_York")
# Hourly markets use the asset's name in a human-readable slug.
_HOURLY_NAMES = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "xrp",
    "DOGE": "dogecoin", "BNB": "bnb", "HYPE": "hype",
}
# Only protocol versions whose ids are verified against the market socket and order API.
_SUPPORTED_VERSIONS = {"v1"}
_warned: set[tuple] = set()


def _warn_once(event: str, key: tuple, **fields) -> None:
    """Discovery reruns every interval; log each distinct problem once, not every run."""
    if (event, key) not in _warned:
        _warned.add((event, key))
        logger.warning(event, **fields)


def interval_start(minutes: int, ts: float) -> int:
    """UTC-aligned start of the interval containing `ts` (5m/15m boundaries are whole)."""
    sec = minutes * 60
    return int(ts) - (int(ts) % sec)


def updown_slug(asset: str, minutes: int, ts: float) -> str:
    """Gamma slug for an asset's up/down interval market, e.g. 'btc-updown-5m-1779449400'."""
    return f"{asset.lower()}-updown-{minutes}m-{interval_start(minutes, ts)}"


def hourly_slug(asset: str, ts: float) -> str | None:
    """Gamma slug for an hourly up/down market, named by the ET hour it starts, e.g.
    'bitcoin-up-or-down-november-10-2026-12am-et' (00:00-01:00 ET). None if unknown asset.

    Unverified: on the autumn DST change the 1am ET hour happens twice and both map to the
    same slug; how Polymarket names the second one is unknown."""
    name = _HOURLY_NAMES.get(asset.upper())
    if name is None:
        return None
    t = datetime.fromtimestamp(interval_start(60, ts), _ET)
    hour = f"{t.hour % 12 or 12}{'am' if t.hour < 12 else 'pm'}"
    return f"{name}-up-or-down-{t.strftime('%B').lower()}-{t.day}-{t.year}-{hour}-et"


def market_slug(asset: str, minutes: int, ts: float) -> str | None:
    """The Gamma slug for an asset's up/down market of `minutes` containing `ts`."""
    if minutes == 60:
        return hourly_slug(asset, ts)
    return updown_slug(asset, minutes, ts)


def _get(url: str, timeout: float = 15.0):
    req = urllib.request.Request(url, headers={"User-Agent": "oraclebook"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _to_market(m: dict) -> Market | None:
    cond = m.get("conditionId")
    if not cond:
        return None
    slug = m.get("slug", "")
    version = m.get("version") or "v1"
    if version not in _SUPPORTED_VERSIONS:
        # Protocol V2 markets are addressed by `positionIds`; whether those work as market
        # socket and order ids is unverified, so skip them rather than trade them blind.
        _warn_once("polymarket_markets.unsupported_version", (slug,), slug=slug, version=version)
        return None
    toks = _json_list(m.get("clobTokenIds"))
    outs = _json_list(m.get("outcomes"))
    if not toks or (outs and len(toks) != len(outs)):
        _warn_once(
            "polymarket_markets.unusable_token_ids", (slug,),
            slug=slug, tokens=len(toks), outcomes=len(outs),
        )
        return None
    meta: dict = {"slug": slug, "version": version}
    taker_only = True
    fee_verified = False
    schedule = m.get("feeSchedule")
    try:
        rate = float(schedule["rate"])
        exponent = float(schedule.get("exponent") or CRYPTO_FEE_EXPONENT)
    except (KeyError, TypeError, ValueError, AttributeError):
        rate = None
    if rate is not None:
        if m.get("feesEnabled") is False:
            rate = 0.0
        # Published by the venue. Note: fee simulation (Venue.fee) still uses the module
        # constants, which is why a differing schedule is flagged.
        meta["fee_type"] = m.get("feeType", "")
        meta["fee_schedule"] = {"rate": rate, "exponent": exponent}
        taker_only = bool(schedule.get("takerOnly", True))
        fee_verified = True
        if (rate, exponent) != (CRYPTO_FEE_RATE, CRYPTO_FEE_EXPONENT):
            _warn_once(
                "polymarket_markets.fee_schedule_differs", (slug,),
                slug=slug, schedule=meta["fee_schedule"], simulated_rate=CRYPTO_FEE_RATE,
            )
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
        token_ids=tuple(str(t) for t in toks),
        outcomes=tuple(outs),
        tick_size=str(m.get("orderPriceMinTickSize", "0.01")),
        min_order_size=float(m.get("orderMinSize") or 0.0),
        end_ts=end_ts,
        fee_taker_only=taker_only,
        fee_verified=fee_verified,
        meta=meta,
    )


def _json_list(value) -> list:
    """Gamma returns some list fields JSON-encoded as strings."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return list(value) if isinstance(value, list) else []


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
            if market_slug(asset, minutes, now) is None:
                _warn_once(
                    "polymarket_markets.unsupported_interval", (asset, minutes),
                    asset=asset, minutes=minutes,
                )
                continue
            found = 0
            for ts in (now, now + minutes * 60):  # current + next interval
                slug = market_slug(asset, minutes, ts)
                if slug is None or slug in seen:
                    continue
                seen.add(slug)
                market = market_by_slug(slug)
                if market is not None:
                    out.append(market)
                    found += 1
            if not found:
                # Polymarket names some intervals differently (hourly markets use
                # human-readable slugs), so an empty result is a naming problem to surface.
                slug = market_slug(asset, minutes, now)
                _warn_once(
                    "polymarket_markets.no_markets_found", (slug,),
                    asset=asset, minutes=minutes, slug=slug,
                )
    return out
