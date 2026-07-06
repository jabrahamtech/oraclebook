"""Bus messages — commands (imperative, can be rejected) and events (facts,
immutable, past-tense). Commands route point-to-point; events fan out.

Topic-string constants live here too, so a typo is an import error, not a silent
drop. Venue-specific messages (e.g. on-chain redemption, venue-WS reconciliation)
live in their adapter, not the kernel.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from uuid import uuid4

from oraclebook.core.models import Fill, Market, OrderRequest, Resolution

# --- topic strings -----------------------------------------------------------
EVT_BOOK_UPDATE = "book_update"
EVT_PRICE_TICK = "price_tick"
EVT_INTERVAL_BOUNDARY = "interval_boundary"
EVT_ORDERS = "orders"
EVT_FILLS = "fills"
EVT_ORDER_REJECTED = "order_rejected"
EVT_BOOK_INVALIDATED = "book_invalidated"
EVT_MARKETS_DISCOVERED = "markets_discovered"
EVT_POSITION_RESOLVED = "position_resolved"


# --- base classes ------------------------------------------------------------
@dataclass(frozen=True)
class Command:
    """Imperative request — can be rejected. Routes to a single handler."""

    id: str = field(default_factory=lambda: uuid4().hex[:8])
    ts: float = field(default_factory=time.time)


@dataclass(frozen=True)
class Event:
    """A fact that happened — immutable, past-tense. Fans out to all subscribers."""

    id: str = field(default_factory=lambda: uuid4().hex[:8])
    ts: float = field(default_factory=time.time)


# --- commands ----------------------------------------------------------------
@dataclass(frozen=True)
class SubmitOrder(Command):
    """Submit orders through gates + execution."""

    orders: tuple[OrderRequest, ...] = ()


# --- market-data events ------------------------------------------------------
@dataclass(frozen=True)
class BookUpdate(Event):
    """Top-of-book (and optional depth) for one outcome token. Coalescing topic
    keyed on `token_id` — only the freshest per token is delivered."""

    token_id: str = ""
    market_id: str = ""
    best_bid: float = 0.0
    best_ask: float = 0.0
    bids: tuple[tuple[float, float], ...] = ()  # (price, size)
    asks: tuple[tuple[float, float], ...] = ()
    age_ms: float = 0.0


@dataclass(frozen=True)
class PriceTick(Event):
    """A reference/exchange price for an asset (e.g. spot used to price an
    interval market). Coalescing topic keyed on `symbol`."""

    symbol: str = ""
    price: float = 0.0


@dataclass(frozen=True)
class IntervalBoundary(Event):
    """An interval market reached its boundary. `close_price` is the settlement
    oracle's price at the boundary — it closes the old interval and opens the next.
    Published by a venue's boundary/settlement service; consumed by the resolution
    service and by strategies for risk guards."""

    asset: str = ""
    interval: int = 0
    interval_start: int = 0
    open_price: float = 0.0
    close_price: float = 0.0


# --- lifecycle events --------------------------------------------------------
@dataclass(frozen=True)
class OrderFilled(Event):
    fills: tuple[Fill, ...] = ()
    orders: tuple[OrderRequest, ...] = ()


@dataclass(frozen=True)
class OrderRejected(Event):
    orders: tuple[OrderRequest, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class OrderCancelFailed(Event):
    order_id: str = ""
    venue_order_id: str = ""


@dataclass(frozen=True)
class CircuitBreakerTripped(Event):
    reason: str = ""


@dataclass(frozen=True)
class MarketsDiscovered(Event):
    markets: tuple[Market, ...] = ()


@dataclass(frozen=True)
class BookInvalidated(Event):
    """Books for these tokens are stale (e.g. feed disconnect) — stop quoting them."""

    token_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PositionResolved(Event):
    """A position settled at a boundary."""

    resolution: Resolution | None = None


@dataclass(frozen=True)
class SignalDetected(Event):
    strategy: str = ""
    asset: str = ""
    interval: int = 0


@dataclass(frozen=True)
class SignalExecuted(Event):
    strategy: str = ""
    asset: str = ""
    interval: int = 0
