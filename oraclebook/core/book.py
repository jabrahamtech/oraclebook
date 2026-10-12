"""Book — the position tracker (aggregate root for positions).

The single place positions mutate: every Fill flows through `on_fill`, every settlement
through `settle`. BUYs add shares at a size-weighted average entry; SELLs reduce the
position (never below zero — prediction-market venues don't allow naked shorts) and drop
it when flat. Realised PnL accumulates here: sell proceeds over average entry, settlement
value over average entry, and every fee paid.
"""
from __future__ import annotations

import structlog

from oraclebook.core.models import Fill, Position, Resolution, Side

logger = structlog.get_logger()

_EPS = 1e-9


class PositionTracker:
    """In-memory position book keyed by outcome token id."""

    def __init__(self) -> None:
        self._positions: dict[str, Position] = {}
        self.realized_pnl = 0.0

    def on_fill(self, fill: Fill) -> None:
        """Apply a fill. The only mutation path for positions besides `settle`."""
        self.realized_pnl -= fill.fee
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
                    meta={"strategy": fill.strategy, "direction": fill.direction},
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
            return
        held = pos.size if pos is not None else 0.0
        if fill.size > held + _EPS:
            logger.warning(
                "book.oversell_clamped", token_id=fill.token_id, sell=fill.size, held=held
            )
        if pos is None:
            return
        qty = min(fill.size, pos.size)
        self.realized_pnl += (fill.price - pos.avg_entry) * qty
        pos.size -= qty
        pos.cumulative_fee += fill.fee
        if pos.size <= _EPS:
            del self._positions[fill.token_id]

    def settle(self, token_id: str, settlement_value: float, ts: float) -> Resolution | None:
        """Resolve a held token at its settlement value (1.0 win / 0.0 loss for a binary
        outcome). Removes the position, books its PnL, and returns the Resolution fact."""
        pos = self._positions.pop(token_id, None)
        if pos is None:
            return None
        pnl = (settlement_value - pos.avg_entry) * pos.size
        self.realized_pnl += pnl
        return Resolution(
            token_id=token_id,
            market_id=pos.market_id,
            strategy=pos.meta.get("strategy", ""),
            direction=pos.meta.get("direction", ""),
            settlement_value=settlement_value,
            pnl=pnl,
            ts=ts,
            meta={"size": pos.size, "avg_entry": pos.avg_entry},
        )

    def get(self, token_id: str) -> Position | None:
        return self._positions.get(token_id)

    def positions(self) -> list[Position]:
        return list(self._positions.values())

    def total_deployed(self) -> float:
        """Sum of avg_entry * size across all open positions (USD notional at entry)."""
        return sum(p.avg_entry * p.size for p in self._positions.values())
