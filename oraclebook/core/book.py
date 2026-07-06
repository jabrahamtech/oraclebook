"""Book — the position tracker (aggregate root for positions).

The single place positions mutate: every Fill flows through `on_fill`. BUYs add shares
at a size-weighted average entry; SELLs reduce the position and drop it when flat. This
is the concrete implementation of the `Book` protocol the gates and App depend on.
"""
from __future__ import annotations

from oraclebook.core.models import Fill, Position, Side

_EPS = 1e-9


class PositionTracker:
    """In-memory position book keyed by outcome token id."""

    def __init__(self) -> None:
        self._positions: dict[str, Position] = {}

    def on_fill(self, fill: Fill) -> None:
        """Apply a fill. The only mutation path for positions."""
        pos = self._positions.get(fill.token_id)
        if fill.side == Side.BUY:
            if pos is None:
                self._positions[fill.token_id] = Position(
                    token_id=fill.token_id,
                    market_id=fill.market_id,
                    side=Side.BUY,
                    size=fill.size,
                    avg_entry=fill.price,
                    cumulative_fee=fill.fee,
                )
            else:
                new_size = pos.size + fill.size
                pos.avg_entry = (
                    (pos.avg_entry * pos.size + fill.price * fill.size) / new_size
                    if new_size > _EPS
                    else 0.0
                )
                pos.size = new_size
                pos.cumulative_fee += fill.fee
        elif pos is not None:  # SELL reduces an existing position
            pos.size -= fill.size
            pos.cumulative_fee += fill.fee
            if pos.size <= _EPS:
                del self._positions[fill.token_id]

    def get(self, token_id: str) -> Position | None:
        return self._positions.get(token_id)

    def positions(self) -> list[Position]:
        return list(self._positions.values())

    def total_deployed(self) -> float:
        """Sum of avg_entry * size across all open positions (USD notional at entry)."""
        return sum(p.avg_entry * p.size for p in self._positions.values())
