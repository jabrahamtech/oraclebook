"""A mock Venue + Feed for demos, tests, and the quickstart — no network, no keys.

`MockVenue` implements the `Venue` protocol and `MockFeed` publishes synthetic
`BookUpdate`s on a timer, so you can run the full App loop end-to-end against a fake
market. Use it as the smallest possible reference for what a real adapter implements.
"""
from __future__ import annotations

import asyncio
import random

from oraclebook.core.book import PositionTracker
from oraclebook.core.bus import Bus
from oraclebook.core.events import EVT_BOOK_UPDATE, BookUpdate
from oraclebook.core.models import Market
from oraclebook.core.paper_executor import PaperExecutor
from oraclebook.core.protocols import Executor, Feed


class MockFeed:
    """Publishes a synthetic two-sided book whose mid random-walks around 0.5."""

    def __init__(self, bus: Bus, market_id: str = "MOCK-BTC-5m", interval_sec: float = 0.1) -> None:
        self._bus = bus
        self._market_id = market_id
        self._interval = interval_sec
        self._task: asyncio.Task | None = None
        self._yes = f"{market_id}:YES"
        self._no = f"{market_id}:NO"

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()

    async def _loop(self) -> None:
        mid = 0.5
        while True:
            mid = min(0.95, max(0.05, mid + random.uniform(-0.05, 0.05)))
            await self._bus.publish(
                EVT_BOOK_UPDATE,
                BookUpdate(token_id=self._yes, market_id=self._market_id,
                           best_bid=round(mid - 0.01, 3), best_ask=round(mid + 0.01, 3)),
            )
            await self._bus.publish(
                EVT_BOOK_UPDATE,
                BookUpdate(token_id=self._no, market_id=self._market_id,
                           best_bid=round(1 - mid - 0.01, 3), best_ask=round(1 - mid + 0.01, 3)),
            )
            await asyncio.sleep(self._interval)


class MockVenue:
    """A fake prediction-market venue for demos. Implements the `Venue` protocol."""

    name = "mock"

    def __init__(self, market_id: str = "MOCK-BTC-5m") -> None:
        self._market_id = market_id

    def feed(self, bus: Bus) -> Feed:
        return MockFeed(bus, market_id=self._market_id)

    def executor(self) -> Executor:
        # No live venue — paper only. The App uses executor="paper" anyway.
        return PaperExecutor(book=PositionTracker())

    async def current_markets(self) -> list[Market]:
        return [Market(venue=self.name, market_id=self._market_id, condition_id=self._market_id,
                       token_ids=(f"{self._market_id}:YES", f"{self._market_id}:NO"),
                       outcomes=("Yes", "No"))]

    def fee(self, price: float, size: float, is_maker: bool) -> float:
        return 0.0
