"""App — the composition root and runtime.

The one place that wires concrete types together: a Feed (from a Venue), a Strategy, a
chain of risk Gates, the Book, and an Executor (paper or live). It subscribes the
strategy's handlers to the bus, and for each order-producing event it runs the orders
through the Gates and the Executor, then applies fills to the Book and republishes them.

Order flow is serialised: gate → submit → apply-fills runs under one lock, so concurrent
batches from different handlers can't all pass a gate against the same stale snapshot.
Realised PnL comes from the Book (trading + settlement + fees) and resets at each UTC day.
In replay mode handlers run inline on publish, so every recorded event is fully processed
before the replay clock advances — no lookahead, exact cross-topic ordering.

Usage (script): `App(venue=..., strategy=..., executor="paper").run()`.
Usage (embedded): `await app.start()` … `await app.stop()`.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

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
    BookUpdate,
    OrderFilled,
    OrderRejected,
    PositionResolved,
)
from oraclebook.core.models import Fill, OrderRequest, Resolution, RiskContext
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
        self.replay = replay
        if replay:
            self.bus = Bus()
        else:
            self.bus = Bus(
                coalescing={EVT_BOOK_UPDATE: "token_id", EVT_PRICE_TICK: "symbol"},
                critical=frozenset({EVT_FILLS, EVT_ORDER_REJECTED, EVT_POSITION_RESOLVED}),
            )
        self.book = book or PositionTracker()
        self.ledger = ledger
        self.strategy = strategy
        self.gates = list(gates or [])
        self.venue = venue
        self.feed = feed or (venue.feed(self.bus) if venue is not None else None)
        if executor == "paper":
            self.executor: Executor = PaperExecutor(
                self.book,
                starting_balance=starting_balance,
                clock=self.clock,
                fee_model=venue.fee if venue is not None else None,
            )
        elif executor == "live":
            if venue is None:
                raise ValueError("executor='live' requires a venue")
            self.executor = venue.executor()
        else:
            self.executor = executor  # a concrete Executor instance
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._order_lock = asyncio.Lock()
        self._day = self._utc_day()
        self._day_start_realized = self.book.realized_pnl
        self._rejections_seen = 0

    @property
    def daily_pnl(self) -> float:
        """Realised PnL (trades + settlements − fees) since the start of the current UTC day."""
        day = self._utc_day()
        if day != self._day:
            self._day = day
            self._day_start_realized = self.book.realized_pnl
        return self.book.realized_pnl - self._day_start_realized

    def _utc_day(self) -> str:
        return datetime.fromtimestamp(self.clock.now(), tz=timezone.utc).date().isoformat()

    async def start(self) -> None:
        """Wire handlers, spawn consumers, and start the feed. Returns once running."""
        self._running = True
        await self.strategy.start()
        self._wire(EVT_BOOK_UPDATE, self._on_book, produces_orders=True)
        self._wire(EVT_PRICE_TICK, self.strategy.on_price, produces_orders=True)
        self._wire(EVT_INTERVAL_BOUNDARY, self.strategy.on_boundary, produces_orders=True)
        self._wire(EVT_FILLS, self.strategy.on_fill, produces_orders=False)
        self._wire(EVT_POSITION_RESOLVED, self.strategy.on_resolution, produces_orders=False)
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
        """Stop the feed first (no new data), then the consumers, then pull every resting
        order so nothing is left working on the venue after shutdown."""
        self._running = False
        if self.feed is not None:
            await self.feed.stop()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        try:
            cancelled = await self.executor.cancel_all()
            if cancelled:
                logger.info("app.cancelled_resting_orders", n=cancelled)
        except Exception:
            logger.exception("app.cancel_all_failed")
        await self.strategy.stop()
        logger.info("app.stopped")

    async def resolve(self, token_id: str, settlement_value: float) -> Resolution | None:
        """Settle a held outcome token (1.0 win / 0.0 loss): books PnL in the Book, credits a
        paper account, records the Resolution, and publishes `PositionResolved`."""
        resolution = self.book.settle(token_id, settlement_value, ts=self.clock.now())
        if resolution is None:
            return None
        credit = getattr(self.executor, "credit", None)
        if credit is not None:
            credit(settlement_value * resolution.meta["size"])
        if self.ledger is not None:
            self.ledger.record_resolution(resolution)
        await self.bus.publish(EVT_POSITION_RESOLVED, PositionResolved(resolution=resolution))
        return resolution

    # --- internals -----------------------------------------------------------
    def _wire(
        self, topic: str, handler: Callable[..., Awaitable], *, produces_orders: bool
    ) -> None:
        async def handle(event) -> None:
            try:
                result = await handler(event)
                if produces_orders and result:
                    await self._process(result)
            except Exception:
                logger.exception("app.handler_error", handler=getattr(handler, "__name__", "?"))

        if self.replay:
            self.bus.add_handler(topic, handle)
            return
        sub = self.bus.subscribe(topic)
        self._tasks.append(asyncio.create_task(self._consume(sub, handle)))

    async def _consume(self, sub, handle: Callable[..., Awaitable]) -> None:
        async for event in Bus.consume(sub, running=lambda: self._running):
            await handle(event)

    async def _on_book(self, update: BookUpdate) -> list[OrderRequest]:
        """Let a simulated executor match working orders against the new book before the
        strategy sees it, then hand the book to the strategy."""
        on_book = getattr(self.executor, "on_book", None)
        if on_book is not None:
            async with self._order_lock:
                await self._apply(on_book(update), ())
                await self._publish_rejections()
        return await self.strategy.on_book(update)

    async def _process(self, orders: list[OrderRequest]) -> None:
        """Gate the batch, execute survivors, apply fills to the Book, republish."""
        from oraclebook.risk.gates import run_gates

        async with self._order_lock:
            open_orders = getattr(self.executor, "open_orders", None)
            ctx = RiskContext(
                balance=await self.executor.get_balance(),
                daily_pnl=self.daily_pnl,
                book=self.book,
                paper_mode=isinstance(self.executor, PaperExecutor),
                open_orders=tuple(open_orders()) if open_orders is not None else (),
            )
            check = run_gates(self.gates, orders, ctx)
            if not check.passed:
                await self.bus.publish(
                    EVT_ORDER_REJECTED, OrderRejected(orders=tuple(orders), reason=check.reason)
                )
                logger.info(
                    "app.order_rejected", gate=check.gate, reason=check.reason, n=len(orders)
                )
                return
            await self._apply(await self.executor.submit(orders), orders)
            await self._publish_rejections()

    async def _apply(self, fills: list[Fill], orders) -> None:
        for f in fills:
            self.book.on_fill(f)
            if self.ledger is not None:
                self.ledger.record_fill(f)
        if fills:
            await self.bus.publish(
                EVT_FILLS, OrderFilled(fills=tuple(fills), orders=tuple(orders))
            )

    async def _publish_rejections(self) -> None:
        """Surface orders a simulated venue refused (bad price, no cash, no inventory)."""
        rejections = getattr(self.executor, "rejections", None)
        if not rejections or len(rejections) <= self._rejections_seen:
            return
        new = rejections[self._rejections_seen:]
        self._rejections_seen = len(rejections)
        for order, reason in new:
            await self.bus.publish(
                EVT_ORDER_REJECTED, OrderRejected(orders=(order,), reason=reason)
            )
