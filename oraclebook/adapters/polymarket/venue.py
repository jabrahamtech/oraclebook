"""PolymarketVenue — the reference Venue adapter (one cohesive slice).

Bundles Polymarket's market discovery, fee model, CLOB book feed, and live executor behind
the `Venue` protocol. Discovery, fees, and the feed are credential-free (public Gamma/CLOB
APIs), so paper trading, recording, and Replay work against real Polymarket data out of the
box. Live execution (`PolymarketExecutor`, via py-clob-client) reads credentials from env and
is ⚠️ experimental — validate in paper + Replay before risking real money.
"""
from __future__ import annotations

import asyncio

from oraclebook.adapters.polymarket.feed import PolymarketFeed
from oraclebook.adapters.polymarket.fees import fee_amount
from oraclebook.adapters.polymarket.markets import discover
from oraclebook.core.bus import Bus
from oraclebook.core.models import Market
from oraclebook.core.protocols import Executor, Feed


class PolymarketVenue:
    """Implements the `Venue` protocol over Polymarket's public CLOB/Gamma APIs."""

    name = "polymarket"

    def __init__(self, assets: list[str], intervals: list[int]) -> None:
        self._assets = [a.upper() for a in assets]
        self._intervals = list(intervals)

    def feed(self, bus: Bus) -> Feed:
        return PolymarketFeed(bus, self._assets, self._intervals)

    def executor(self) -> Executor:
        # Live CLOB executor; reads credentials from env (raises a clear error if missing).
        # ⚠️ Live trading risks real money — validate in paper + Replay first.
        from oraclebook.adapters.polymarket.executor import PolymarketExecutor

        return PolymarketExecutor()

    async def current_markets(self) -> list[Market]:
        return await asyncio.to_thread(discover, self._assets, self._intervals)

    def fee(self, price: float, size: float, is_maker: bool) -> float:
        return fee_amount(price, size, is_maker=is_maker)
