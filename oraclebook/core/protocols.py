"""oraclebook public API — the protocol seams a venue adapter or strategy implements.

These are intentionally minimal, structural (PEP 544) protocols so adapters and
strategies depend on *behaviour*, not concrete classes. The composition root
(`oraclebook.core.app.App`) is the only place that wires concrete types.

Generalised from the original Polymarket bot's `core/protocols.py`; Polymarket is
now one implementation of `PredictionVenue`, not a hard dependency of the core.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from oraclebook.core.models import (
    Fill,
    Market,
    OrderRequest,
    Position,
    Resolution,
    RiskCheck,
    RiskContext,
)

if TYPE_CHECKING:
    from oraclebook.core.bus import Bus


@runtime_checkable
class Clock(Protocol):
    """Time source. `RealClock` in production, `ReplayClock` in backtests, so all
    decision-path code reads `self._clock.now()` and a single-feed replay is reproducible."""

    def now(self) -> float: ...  # unix seconds, UTC


@runtime_checkable
class Feed(Protocol):
    """A market-data source. Publishes domain events (book updates, prices,
    interval boundaries) onto the MessageBus. One per venue/data source."""

    async def start(self) -> None: ...
    async def stop(self) -> None: ...


@runtime_checkable
class Strategy(Protocol):
    """Signal generator. Consumes events (book/price/boundary/fill/resolution),
    emits `OrderRequest`s onto the bus. Strategies never call the executor directly
    and never touch positions outside the Book boundary."""

    name: str

    async def start(self) -> None: ...
    async def stop(self) -> None: ...


@runtime_checkable
class Executor(Protocol):
    """Order lifecycle. Submits/cancels orders on a venue and reports fills.
    Implementations: `PaperExecutor` (simulated) and per-venue live executors."""

    async def submit(self, orders: list[OrderRequest]) -> list[Fill]: ...
    async def cancel(self, venue_order_id: str) -> bool: ...
    async def cancel_all(self) -> int: ...
    async def get_balance(self) -> float: ...
    def get_position(self, token_id: str) -> Position | None: ...


@runtime_checkable
class Gate(Protocol):
    """A single pre-trade risk check. Gates are ordered cheapest-first and the chain
    short-circuits on the first failure. Each gate carries its own limits; the live
    account state arrives via `ctx`."""

    name: str

    def check(self, orders: list[OrderRequest], ctx: RiskContext) -> RiskCheck: ...


@runtime_checkable
class Book(Protocol):
    """Aggregate root for positions + account — the only place positions mutate."""

    def on_fill(self, fill: Fill) -> None: ...
    def get(self, token_id: str) -> Position | None: ...
    def positions(self) -> list[Position]: ...
    def total_deployed(self) -> float: ...  # sum of avg_entry * size across positions


@runtime_checkable
class ResolutionSink(Protocol):
    """Receives `Resolution` facts when an interval/market settles — strategies
    consume these for risk guards and PnL; the event log persists them."""

    def record_resolution(self, resolution: Resolution) -> None: ...


@runtime_checkable
class MetricsSink(Protocol):
    """Receives fills/resolutions/rejections for metrics + monitoring."""

    def record_fill(self, fill: Fill) -> None: ...
    def record_resolution(self, resolution: Resolution) -> None: ...


@runtime_checkable
class Venue(Protocol):
    """The seam for adding a prediction-market venue (Polymarket is the reference).

    Bundles everything venue-specific: how to discover markets, the data Feed, the
    Executor, the settlement/Resolution source, and the instrument/fee model. The
    core kernel orchestrates these via the bus without knowing the venue."""

    name: str

    def feed(self, bus: "Bus") -> Feed: ...  # a Feed that publishes onto this bus
    def executor(self) -> Executor: ...
    async def current_markets(self) -> list[Market]: ...
    def fee(self, price: float, size: float, is_maker: bool) -> float: ...
