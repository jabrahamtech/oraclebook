"""DepthPaperExecutor — paper fills that pay for the book they hit.

`PaperExecutor` fills every order instantly at its limit price for full size, which flatters
any strategy that trades *after* someone else (copy-trading, momentum, news). This model:

- **Walks the recorded depth.** A marketable order consumes ask (BUY) or bid (SELL) levels
  up to its limit and fills at the size-weighted average price. Liquidity it takes is gone
  for later orders on the same snapshot.
- **Applies latency.** With `latency_ms > 0` an order reaches the venue `latency_ms` after
  submission and fills against the book as it stood at that moment, not at decision time.
- **Fills partially.** FOK fills fully or not at all; FAK/MARKET take what is there and drop
  the rest; GTC/GTD rest the remainder.
- **Rests conservatively.** A resting order fills only when a later book trades through it
  (BUY: asks at or below its price), at its own price, as a maker, for at most the size
  shown there — back-of-queue, no credit for queue position.

A book update with no depth (top-of-book only) marks that token's depth unknown, so nothing
fills against it until the next full snapshot. Fill `meta` carries `requested`, `ref_price`
(the touch at decision time), `slippage` (per share, positive = worse than the touch),
`latency_ms` and `liquidity` (`taker` / `maker`). Orders that end without filling in full
are listed in `unfilled` with the reason.

The App feeds this executor every book update via `on_book` before the strategy sees it, and
publishes any fills that returns. Delayed fills therefore reach the strategy on the first book
update for that token at or after their arrival time; their `ts` is the arrival time.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import structlog

from oraclebook.core.book import PositionTracker
from oraclebook.core.events import BookUpdate
from oraclebook.core.models import Fill, OrderRequest, OrderType, Side, TimeInForce
from oraclebook.core.paper_executor import PaperExecutor
from oraclebook.core.protocols import Clock

logger = structlog.get_logger()

_EPS = 1e-9

FeeFn = Callable[[float, float, bool], float]  # (price, size, is_maker) -> USD, as Venue.fee


@dataclass
class _Working:
    order: OrderRequest
    remaining: float
    ref_price: float | None
    arrive_ts: float


@dataclass(frozen=True)
class Unfilled:
    order: OrderRequest
    size: float
    reason: str


class DepthPaperExecutor(PaperExecutor):
    """Paper executor that fills against recorded depth, with latency and partial fills."""

    def __init__(
        self,
        book: PositionTracker,
        starting_balance: float = 1000.0,
        *,
        latency_ms: float = 0.0,
        fee: FeeFn | None = None,
        clock: Clock | None = None,
    ) -> None:
        super().__init__(book, starting_balance=starting_balance, clock=clock)
        if latency_ms < 0:
            raise ValueError("latency_ms must be >= 0")
        self._latency = latency_ms / 1000.0
        self._fee: FeeFn = fee or (lambda price, size, is_maker: 0.0)
        # token -> {"bids": [[price, size], ...] best-first, "asks": [...]}; None = unknown
        self._depth: dict[str, dict[str, list[list[float]]] | None] = {}
        self._in_flight: list[_Working] = []
        self._resting: dict[str, _Working] = {}
        self.unfilled: list[Unfilled] = []

    @property
    def pending(self) -> int:
        """Orders still in flight or resting."""
        return len(self._in_flight) + len(self._resting)

    async def submit(self, orders: list[OrderRequest]) -> list[Fill]:
        now = self._clock.now()
        fills: list[Fill] = []
        for o in orders:
            w = _Working(o, o.size, self._touch(o), now + self._latency)
            if self._latency > 0:
                self._in_flight.append(w)
            else:
                fills.extend(self._arrive(w))
        return fills

    def on_book(self, update: BookUpdate) -> list[Fill]:
        """Advance simulated time to this book update and return any fills it produces."""
        token, ts = update.token_id, update.ts
        fills: list[Fill] = []
        due = [w for w in self._in_flight if w.order.token_id == token and w.arrive_ts < ts]
        for w in due:
            self._in_flight.remove(w)
            fills.extend(self._arrive(w))

        if update.bids or update.asks:
            self._depth[token] = {
                "bids": [[p, s] for p, s in sorted(update.bids, key=lambda lv: -lv[0]) if s > 0],
                "asks": [[p, s] for p, s in sorted(update.asks, key=lambda lv: lv[0]) if s > 0],
            }
        else:
            self._depth[token] = None

        for oid, w in list(self._resting.items()):
            if w.order.token_id != token:
                continue
            if w.order.expiration and ts >= w.order.expiration:
                del self._resting[oid]
                self._miss(w, "expired")
                continue
            fill = self._match_resting(w, ts)
            if fill is not None:
                fills.append(fill)
            if w.remaining <= _EPS:
                del self._resting[oid]
        return fills

    async def cancel(self, venue_order_id: str) -> bool:
        w = self._resting.pop(venue_order_id, None)
        if w is None:
            w = next((x for x in self._in_flight if x.order.id == venue_order_id), None)
            if w is None:
                return False
            self._in_flight.remove(w)
        self._miss(w, "cancelled")
        return True

    async def cancel_all(self) -> int:
        working = [*self._in_flight, *self._resting.values()]
        self._in_flight.clear()
        self._resting.clear()
        for w in working:
            self._miss(w, "cancelled")
        return len(working)

    # --- matching ------------------------------------------------------------
    def _arrive(self, w: _Working) -> list[Fill]:
        """The order reaches the venue: take liquidity, then rest or drop the remainder."""
        o = w.order
        depth = self._depth.get(o.token_id)
        if depth is None:
            if self._rests(o):
                self._resting[o.id] = w
            else:
                self._miss(w, "book_unknown")
            return []
        levels = depth["asks"] if o.side == Side.BUY else depth["bids"]
        crossable = [lv for lv in levels if self._crosses(o, lv[0])]
        available = sum(s for _, s in crossable)
        if o.time_in_force == TimeInForce.FOK and available + _EPS < w.remaining:
            self._miss(w, "fok_insufficient_depth")
            return []

        taken: list[tuple[float, float]] = []
        for lv in crossable:
            if w.remaining <= _EPS:
                break
            qty = min(w.remaining, lv[1])
            lv[1] -= qty
            w.remaining -= qty
            taken.append((lv[0], qty))
        levels[:] = [lv for lv in levels if lv[1] > _EPS]

        fills = [self._fill(w, taken, w.arrive_ts, is_maker=False)] if taken else []
        if w.remaining > _EPS:
            if self._rests(o):
                self._resting[o.id] = w
            else:
                self._miss(w, "no_depth")
        return fills

    def _match_resting(self, w: _Working, ts: float) -> Fill | None:
        """Trade-through: fill at our price only for size the book shows through it."""
        depth = self._depth.get(w.order.token_id)
        if depth is None:
            return None
        o = w.order
        levels = depth["asks"] if o.side == Side.BUY else depth["bids"]
        qty = 0.0
        for lv in levels:
            if w.remaining - qty <= _EPS or not self._crosses(o, lv[0]):
                break
            take = min(w.remaining - qty, lv[1])
            lv[1] -= take
            qty += take
        levels[:] = [lv for lv in levels if lv[1] > _EPS]
        if qty <= _EPS:
            return None
        w.remaining -= qty
        return self._fill(w, [(o.price, qty)], ts, is_maker=True)

    def _fill(
        self, w: _Working, taken: list[tuple[float, float]], ts: float, *, is_maker: bool
    ) -> Fill:
        o = w.order
        size = sum(q for _, q in taken)
        notional = sum(p * q for p, q in taken)
        vwap = notional / size
        fee = sum(self._fee(p, q, is_maker) for p, q in taken)
        self._balance += (-notional - fee) if o.side == Side.BUY else (notional - fee)
        meta = {
            "requested": o.size,
            "ref_price": w.ref_price,
            "latency_ms": self._latency * 1000.0,
            "liquidity": "maker" if is_maker else "taker",
            "levels": len(taken),
        }
        if w.ref_price is not None:
            sign = 1.0 if o.side == Side.BUY else -1.0
            meta["slippage"] = sign * (vwap - w.ref_price)
        return Fill(
            order_id=o.id,
            token_id=o.token_id,
            market_id=o.market_id,
            side=o.side,
            price=vwap,
            size=size,
            fee=fee,
            ts=ts,
            venue_order_id=o.id,
            strategy=o.strategy,
            asset=o.asset,
            direction=o.direction,
            fill_source="paper",
            meta=meta,
        )

    # --- helpers -------------------------------------------------------------
    def _touch(self, o: OrderRequest) -> float | None:
        """Best opposite price at decision time — the reference for slippage."""
        depth = self._depth.get(o.token_id)
        if not depth:
            return None
        levels = depth["asks"] if o.side == Side.BUY else depth["bids"]
        return levels[0][0] if levels else None

    @staticmethod
    def _crosses(o: OrderRequest, level_price: float) -> bool:
        if o.order_type == OrderType.MARKET:
            return True
        if o.side == Side.BUY:
            return level_price <= o.price + _EPS
        return level_price >= o.price - _EPS

    @staticmethod
    def _rests(o: OrderRequest) -> bool:
        return o.order_type == OrderType.LIMIT and o.time_in_force in (
            TimeInForce.GTC,
            TimeInForce.GTD,
        )

    def _miss(self, w: _Working, reason: str) -> None:
        self.unfilled.append(Unfilled(w.order, w.remaining, reason))
        logger.debug(
            "depth_paper.unfilled", order_id=w.order.id, size=w.remaining, reason=reason
        )
