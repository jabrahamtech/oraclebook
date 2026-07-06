"""Polymarket adapter — the reference Venue.

`PolymarketVenue` bundles credential-free market discovery, the dynamic fee model, and the
CLOB book feed (so paper trading / recording / Replay work against real Polymarket data),
plus `PolymarketExecutor` for ⚠️ experimental live order placement (env creds).

Optional deps: `pip install oraclebook[polymarket]` (aiohttp, read-only feed) or
`oraclebook[polymarket-live]` (adds py-clob-client for live execution).
"""
from oraclebook.adapters.polymarket.executor import PolymarketExecutor
from oraclebook.adapters.polymarket.feed import PolymarketFeed
from oraclebook.adapters.polymarket.fees import fee_amount, taker_fee_per_share
from oraclebook.adapters.polymarket.markets import discover, market_by_slug, updown_slug
from oraclebook.adapters.polymarket.venue import PolymarketVenue

__all__ = [
    "PolymarketVenue",
    "PolymarketExecutor",
    "PolymarketFeed",
    "discover",
    "market_by_slug",
    "updown_slug",
    "fee_amount",
    "taker_fee_per_share",
]
