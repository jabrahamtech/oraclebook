"""BaseStrategy — subclass this and implement only the event handlers you need.

The App dispatches bus events to these handlers. Order-producing handlers (`on_book`,
`on_price`, `on_boundary`) return a list of `OrderRequest`s; the App runs them through
the risk Gates and the Executor. Notify handlers (`on_fill`, `on_resolution`) update
your own state and return nothing. A strategy never calls the executor or mutates the
Book directly — it only emits orders.
"""
from __future__ import annotations

from oraclebook.core.events import (
    BookUpdate,
    IntervalBoundary,
    OrderFilled,
    PositionResolved,
    PriceTick,
)
from oraclebook.core.models import OrderRequest


class BaseStrategy:
    """Default no-op handlers; override the ones your strategy reacts to."""

    name: str = "base"

    async def start(self) -> None:
        """Called once before the event loop starts. Override for setup."""

    async def stop(self) -> None:
        """Called once on shutdown. Override for teardown."""

    # --- order-producing handlers (return orders to submit) ------------------
    async def on_book(self, book: BookUpdate) -> list[OrderRequest]:
        return []

    async def on_price(self, tick: PriceTick) -> list[OrderRequest]:
        return []

    async def on_boundary(self, boundary: IntervalBoundary) -> list[OrderRequest]:
        return []

    # --- notify handlers (update internal state; return nothing) -------------
    async def on_fill(self, event: OrderFilled) -> None:
        ...

    async def on_resolution(self, event: PositionResolved) -> None:
        ...
