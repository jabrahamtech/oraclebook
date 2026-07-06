"""App — the composition root and runtime.

The one place that wires concrete types together: a Feed (from a Venue), a Strategy, a
chain of risk Gates, the Book, and an Executor (paper or live). It subscribes the
strategy's handlers to the bus, and for each order-producing event it runs the orders
through the Gates and the Executor, then applies fills to the Book and republishes them.

Usage (script): `App(venue=..., strategy=..., executor="paper").run()`.
Usage (embedded): `await app.start()` … `await app.stop()`.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import structlog

from oraclebook.core.book import PositionTracker
from oraclebook.core.bus import Bus
from oraclebook.core.clock import RealClock
from oraclebook.core.events import (
    EVT_BOOK_UPDATE,
    EVT_FILLS,
    EVT_INTERVAL_BOUNDARY,
    EVT_ORDER_REJECTED,
    EVT_POSITION_RESOLVED,
    EVT_PRICE_TICK,
    OrderFilled,
    OrderRejected,
)
from oraclebook.core.models import OrderRequest, RiskContext
from oraclebook.core.paper_executor import PaperExecutor
from oraclebook.core.protocols import Clock, Executor, Feed, MetricsSink, Strategy, Venue

logger = structlog.get_logger()


class App:
    """Wires a Venue/Feed + Strategy + Gates + Executor + Book and runs the event loop."""

    def __init__(
        self,
        *,
        strategy: Strategy,
        venue: Venue | None = None,
        feed: Feed | None = None,
        executor: Executor | str = "paper",
        gates: list | None = None,
        book: PositionTracker | None = None,
        clock: Clock | None = None,
        starting_balance: float = 1000.0,
        ledger: MetricsSink | None = None,
        replay: bool = False,
    ) -> None:
        self.clock: Clock = clock or RealClock()
        if replay:
            # Replay must neither coalesce nor drop: every recorded tick is delivered in
            # order, so a backtest re-runs the exact order flow that was recorded.
            self.bus = Bus(
                critical=frozenset(
                    {EVT_BOOK_UPDATE, EVT_PRICE_TICK, EVT_INTERVAL_BOUNDARY,
                     EVT_FILLS, EVT_ORDER_REJECTED}
                )
            )
        else:
            self.bus = Bus(
                coalescing={EVT_BOOK_UPDATE: "token_id", EVT_PRICE_TICK: "symbol"},
                critical=frozenset({EVT_FILLS, EVT_ORDER_REJECTED}),
            )
        self.book = book or PositionTracker()
        self.ledger = ledger
        self.strategy = strategy
        self.gates = list(gates or [])
        self.venue = venue
        self.feed = feed or (venue.feed(self.bus) if venue is not None else None)
        if executor == "paper":
            self.executor: Executor = PaperExecutor(
                self.book, starting_balance=starting_balance, clock=self.clock
            )
        elif executor == "live":
            if venue is None:
                raise ValueError("executor='live' requires a venue")
            self.executor = venue.executor()
        else:
            self.executor = executor  # a concrete Executor instance
        self._running = False
        self._daily_pnl = 0.0
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        """Wire handlers, spawn consumers, and start the feed. Returns once running."""
        self._running = True
        await self.strategy.start()
        self._spawn(EVT_BOOK_UPDATE, self.strategy.on_book, produces_orders=True)
        self._spawn(EVT_PRICE_TICK, self.strategy.on_price, produces_orders=True)
        self._spawn(EVT_INTERVAL_BOUNDARY, self.strategy.on_boundary, produces_orders=True)
        self._spawn(EVT_FILLS, self.strategy.on_fill, produces_orders=False)
        self._spawn(EVT_POSITION_RESOLVED, self._on_resolution, produces_orders=False)
        if self.feed is not None:
            await self.feed.start()
        logger.info(
            "app.started",
            strategy=self.strategy.name,
            gates=len(self.gates),
            executor=type(self.executor).__name__,
        )

    async def arun(self) -> None:
        """Run until stopped (blocks on the consumer tasks)."""
        await self.start()
        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            pass

    def run(self) -> None:
        """Blocking entry point for scripts: `App(...).run()`."""
        asyncio.run(self.arun())

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        if self.feed is not None:
            await self.feed.stop()
        await self.strategy.stop()
        logger.info("app.stopped")

    # --- internals -----------------------------------------------------------
    def _spawn(
        self, topic: str, handler: Callable[..., Awaitable], *, produces_orders: bool
    ) -> None:
        sub = self.bus.subscribe(topic)
        self._tasks.append(asyncio.create_task(self._consume(sub, handler, produces_orders)))

    async def _consume(self, sub, handler: Callable[..., Awaitable], produces_orders: bool) -> None:
        async for event in Bus.consume(sub, running=lambda: self._running):
            try:
                result = await handler(event)
            except Exception:
                logger.exception("app.handler_error", handler=getattr(handler, "__name__", "?"))
                continue
            if produces_orders and result:
                await self._process(result)

    async def _process(self, orders: list[OrderRequest]) -> None:
        """Gate the batch, execute survivors, apply fills to the Book, republish."""
        from oraclebook.risk.gates import run_gates

        ctx = RiskContext(
            balance=await self.executor.get_balance(),
            daily_pnl=self._daily_pnl,
            book=self.book,
            paper_mode=isinstance(self.executor, PaperExecutor),
        )
        check = run_gates(self.gates, orders, ctx)
        if not check.passed:
            await self.bus.publish(
                EVT_ORDER_REJECTED, OrderRejected(orders=tuple(orders), reason=check.reason)
            )
            logger.info("app.order_rejected", gate=check.gate, reason=check.reason, n=len(orders))
            return
        fills = await self.executor.submit(orders)
        for f in fills:
            self.book.on_fill(f)
            if self.ledger is not None:
                self.ledger.record_fill(f)
        if fills:
            await self.bus.publish(
                EVT_FILLS, OrderFilled(fills=tuple(fills), orders=tuple(orders))
            )

    async def _on_resolution(self, event) -> None:
        """Accumulate realised daily PnL from settlements (this is what drives DailyLossGate),
        then notify the strategy. Note: no built-in resolution service publishes
        `PositionResolved` yet, so until you wire one (or set `self._daily_pnl`), DailyLossGate
        has no PnL to act on."""
        if getattr(event, "resolution", None) is not None:
            self._daily_pnl += event.resolution.pnl
        await self.strategy.on_resolution(event)
