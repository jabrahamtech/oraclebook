"""PaperExecutor — simulated order execution for paper trading and backtests.

A simple, venue-agnostic fill model: each submitted order fills immediately at its limit
price for its full size (a SELL only up to the shares held), adjusting a simulated cash
balance (BUY debits, SELL credits, plus an optional fee). This is the baseline for validating strategy logic and for Replay;
a venue's *live* executor (with real maker queues and partial fills) lives in its adapter.
"""
from __future__ import annotations

import structlog

from oraclebook.core.book import PositionTracker
from oraclebook.core.clock import RealClock
from oraclebook.core.models import Fill, OrderRequest, Position, Side
from oraclebook.core.protocols import Clock

logger = structlog.get_logger()


class PaperExecutor:
    """Fills every order at its limit price; tracks a simulated USD balance."""

    def __init__(
        self,
        book: PositionTracker,
        starting_balance: float = 1000.0,
        fee_rate: float = 0.0,
        clock: Clock | None = None,
    ) -> None:
        self._book = book
        self._balance = starting_balance
        self._fee_rate = fee_rate
        self._clock: Clock = clock or RealClock()

    async def submit(self, orders: list[OrderRequest]) -> list[Fill]:
        fills: list[Fill] = []
        sold: dict[str, float] = {}
        for o in orders:
            size = o.size
            if o.side == Side.SELL:  # can't sell shares we don't hold (no shorting outcome tokens)
                pos = self._book.get(o.token_id)
                held = (pos.size if pos is not None else 0.0) - sold.get(o.token_id, 0.0)
                size = min(size, max(0.0, held))
                if size <= 1e-9:
                    logger.debug("paper.sell_without_inventory", order_id=o.id, token=o.token_id)
                    continue
                sold[o.token_id] = sold.get(o.token_id, 0.0) + size
            notional = o.price * size
            fee = notional * self._fee_rate
            self._balance += (-notional - fee) if o.side == Side.BUY else (notional - fee)
            fills.append(
                Fill(
                    order_id=o.id,
                    token_id=o.token_id,
                    market_id=o.market_id,
                    side=o.side,
                    price=o.price,
                    size=size,
                    fee=fee,
                    ts=self._clock.now(),
                    strategy=o.strategy,
                    asset=o.asset,
                    direction=o.direction,
                    fill_source="paper",
                )
            )
        return fills

    async def cancel(self, venue_order_id: str) -> bool:
        return True  # nothing rests in the immediate-fill model

    async def cancel_all(self) -> int:
        return 0

    async def get_balance(self) -> float:
        return self._balance

    def get_position(self, token_id: str) -> Position | None:
        return self._book.get(token_id)
