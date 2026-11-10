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
- **Respects cash and inventory.** A BUY fills only what the cash balance (after fees) can pay
  for; a SELL only up to the shares held. Working BUYs reserve cash in `get_balance`, so gates
  see it.
- **Rests conservatively.** A resting order fills only when a later book trades through it
  (BUY: asks at or below its price), at its own price, as a maker, for at most the size
  shown there — back-of-queue, no credit for queue position.

A book update with no depth (top-of-book only) marks that token's depth unknown, so nothing
fills against it until the next full snapshot; a GTC/GTD order that arrives then takes
liquidity from that snapshot, and an IOC order is dropped. Fill `meta` carries `requested`, `ref_price`
(the touch at decision time), `slippage` (per share, positive = worse than the touch),
`latency_ms` and `liquidity` (`taker` / `maker`). Orders that end without filling in full
are listed in `unfilled` with the reason.

The App feeds this executor every book update via `on_book` before the strategy sees it, and
publishes any fills that returns. Simulated time is the `ts` of the latest book update processed
(the clock is only used before the first one): in Replay the shared clock can run ahead of the
event being handled, which would misdate decisions. Delayed fills therefore reach the strategy on the first book
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
        self._blind: list[_Working] = []  # GTC/GTD that arrived while depth was unknown
        self._sold: dict[str, float] = {}  # SELL qty in fills not yet applied to the book
        self._now: float | None = None
        self.unfilled: list[Unfilled] = []

    @property
    def pending(self) -> int:
        """Orders still in flight or resting."""
        return len(self._in_flight) + len(self._resting) + len(self._blind)

    async def get_balance(self) -> float:
        """Cash minus what working BUYs could still spend, so gates see reserved cash."""
        working = [*self._in_flight, *self._resting.values(), *self._blind]
        reserved = sum(w.remaining * w.order.price for w in working if w.order.side == Side.BUY)
        return self._balance - reserved

    async def submit(self, orders: list[OrderRequest]) -> list[Fill]:
        self._sold = {}
        now = self._now if self._now is not None else self._clock.now()
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
        self._now = ts if self._now is None else max(self._now, ts)
        self._sold = {}
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

        if self._depth[token] is not None:
            blind = [w for w in self._blind if w.order.token_id == token]
            for w in blind:
                self._blind.remove(w)
                w.arrive_ts = ts
                fills.extend(self._arrive(w))

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
            for queue in (self._in_flight, self._blind):
                w = next((x for x in queue if x.order.id == venue_order_id), None)
                if w is not None:
                    queue.remove(w)
                    break
            else:
                return False
        self._miss(w, "cancelled")
        return True

    async def cancel_all(self) -> int:
        working = [*self._in_flight, *self._resting.values(), *self._blind]
        self._in_flight.clear()
        self._resting.clear()
        self._blind.clear()
        for w in working:
            self._miss(w, "cancelled")
        return len(working)

    # --- matching ------------------------------------------------------------
    def _arrive(self, w: _Working) -> list[Fill]:
        """The order reaches the venue: take liquidity, then rest or drop the remainder."""
        o = w.order
        if o.expiration and w.arrive_ts >= o.expiration:
            self._miss(w, "expired")
            return []
        depth = self._depth.get(o.token_id)
        if depth is None:
            if self._rests(o):
                self._blind.append(w)  # take liquidity once the next full book shows it
            else:
                self._miss(w, "book_unknown")
            return []
        if o.side == Side.SELL:
            sellable = max(0.0, self._sellable(o.token_id))
            if sellable + _EPS < w.remaining:
                if o.time_in_force == TimeInForce.FOK:
                    self._miss(w, "no_inventory")
                    return []
                self.unfilled.append(Unfilled(o, w.remaining - sellable, "no_inventory"))
                w.remaining = sellable
            if w.remaining <= _EPS:
                return []

        levels = depth["asks"] if o.side == Side.BUY else depth["bids"]
        plan, cash_limited = self._plan(w, levels, maker=False)
        if o.time_in_force == TimeInForce.FOK and sum(q for _, _, q in plan) + _EPS < w.remaining:
            self._miss(w, "insufficient_balance" if cash_limited else "fok_insufficient_depth")
            return []
        fills = self._take(w, levels, plan, w.arrive_ts, is_maker=False)
        if w.remaining > _EPS:
            if cash_limited:
                self._miss(w, "insufficient_balance")
            elif self._rests(o):
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
        plan, _ = self._plan(w, levels, maker=True)
        fills = self._take(w, levels, plan, ts, is_maker=True)
        return fills[0] if fills else None

    def _plan(
        self, w: _Working, levels: list[list[float]], *, maker: bool
    ) -> tuple[list[tuple[float, float, float]], bool]:
        """(level price, fill price, qty) per crossed level, capped by cash and inventory.
        Crossed levels are a best-first prefix of `levels`. Returns (plan, cash_limited)."""
        o = w.order
        want = w.remaining
        if o.side == Side.SELL:
            want = min(want, max(0.0, self._sellable(o.token_id)))
        cash = self._balance
        plan: list[tuple[float, float, float]] = []
        for level_price, size in levels:
            if want <= _EPS or not self._crosses(o, level_price):
                break
            price = o.price if maker else level_price
            qty = min(want, size)
            if o.side == Side.BUY:
                per_share = price + self._fee(price, 1.0, maker)
                if per_share > 0 and cash / per_share + _EPS < qty:
                    qty = max(0.0, cash / per_share)
                    if qty > _EPS:
                        plan.append((level_price, price, qty))
                    return plan, True
                cash -= qty * per_share
            plan.append((level_price, price, qty))
            want -= qty
        return plan, False

    def _take(
        self,
        w: _Working,
        levels: list[list[float]],
        plan: list[tuple[float, float, float]],
        ts: float,
        *,
        is_maker: bool,
    ) -> list[Fill]:
        """Consume the planned liquidity from `levels` and book one fill for it."""
        if not plan:
            return []
        for lv, (_, _, qty) in zip(levels, plan, strict=False):
            lv[1] -= qty
        levels[:] = [lv for lv in levels if lv[1] > _EPS]
        qty = sum(q for _, _, q in plan)
        w.remaining -= qty
        if w.order.side == Side.SELL:
            self._sold[w.order.token_id] = self._sold.get(w.order.token_id, 0.0) + qty
        return [self._fill(w, [(price, q) for _, price, q in plan], ts, is_maker=is_maker)]

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

    def _sellable(self, token_id: str) -> float:
        """Shares held and not already sold in fills the App has yet to apply."""
        pos = self._book.get(token_id)
        return (pos.size if pos is not None else 0.0) - self._sold.get(token_id, 0.0)

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
