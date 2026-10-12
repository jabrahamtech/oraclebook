"""PaperExecutor — simulated order execution for paper trading and backtests.

A conservative, book-aware fill model that never fills an order the recorded market
could not have filled:

* **Marketable orders** (a BUY at or above the best ask, a SELL at or below the best bid)
  take liquidity as a taker, walking the visible depth at each level's price. Liquidity
  taken is consumed until the next book update, so two orders can't both take the same
  size. Without depth, the touch is assumed to cover the order.
* **Resting orders** fill as a maker, at their own limit price, only once the opposite
  side reaches that price — i.e. the whole bid (ask) queue at our level has traded
  through. Touching the best bid is not enough. This is pessimistic about queue position
  and models adverse selection honestly: a resting bid fills when the market moves
  against it.
* `latency_s` delays activation: an order only meets books stamped at or after
  `submit_time + latency_s`.
* FOK fills completely or not at all; FAK/MARKET fill what they can and cancel the rest;
  GTC/GTD rest (GTD expires at `expiration`).
* Cash for BUYs and shares for SELLs are reserved while an order works; an order the
  account can't cover is rejected (recorded in `rejections`), as a venue would.

Fees come from `fee_model(price, size, is_maker)` — pass a Venue's `fee` to charge that
venue's maker/taker schedule. A venue's *live* executor lives in its adapter.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, replace

import structlog

from oraclebook.core.book import PositionTracker
from oraclebook.core.clock import RealClock
from oraclebook.core.events import BookUpdate
from oraclebook.core.models import Fill, OrderRequest, OrderType, Position, Side, TimeInForce
from oraclebook.core.protocols import Clock

logger = structlog.get_logger()

FeeModel = Callable[[float, float, bool], float]

_EPS = 1e-9


def _no_fee(price: float, size: float, is_maker: bool) -> float:
    return 0.0


@dataclass
class _Working:
    order: OrderRequest
    remaining: float
    active_at: float
    reserved_cash: float = 0.0     # BUY: cash held back for the unfilled remainder
    active: bool = False


class PaperExecutor:
    """Simulated venue: conservative fills against the latest book per token."""

    def __init__(
        self,
        book: PositionTracker,
        starting_balance: float = 1000.0,
        clock: Clock | None = None,
        *,
        fee_model: FeeModel | None = None,
        latency_s: float = 0.0,
    ) -> None:
        self._book = book
        self._cash = starting_balance
        self._clock: Clock = clock or RealClock()
        self._fee = fee_model or _no_fee
        self._latency_s = latency_s
        self._books: dict[str, BookUpdate] = {}
        self._consumed: dict[str, dict[float, float]] = {}
        self._working: dict[str, _Working] = {}
        self._reserved_shares: dict[str, float] = {}
        self.rejections: list[tuple[OrderRequest, str]] = []

    # --- Executor protocol ---------------------------------------------------
    async def submit(self, orders: list[OrderRequest]) -> list[Fill]:
        fills: list[Fill] = []
        now = self._clock.now()
        for o in orders:
            reason = self._admit(o, now)
            if reason:
                self._reject(o, reason)
                continue
            w = self._working[o.id]
            if self._latency_s <= 0:
                fills.extend(self._activate(w, self._books.get(o.token_id)))
        return fills

    async def cancel(self, venue_order_id: str) -> bool:
        w = self._working.get(venue_order_id)
        if w is None:
            return False
        self._release(w)
        return True

    async def cancel_all(self) -> int:
        working = list(self._working.values())
        for w in working:
            self._release(w)
        return len(working)

    async def get_balance(self) -> float:
        """Free cash: balance minus cash reserved for working BUY orders."""
        return self._cash - sum(w.reserved_cash for w in self._working.values())

    def get_position(self, token_id: str) -> Position | None:
        return self._book.get(token_id)

    # --- simulation hooks ----------------------------------------------------
    def on_book(self, update: BookUpdate) -> list[Fill]:
        """Feed a market-data update; returns fills it triggers on working orders."""
        self._books[update.token_id] = update
        self._consumed[update.token_id] = {}
        fills: list[Fill] = []
        ts = update.ts
        for w in [w for w in self._working.values() if w.order.token_id == update.token_id]:
            if w.order.expiration and ts >= w.order.expiration:
                self._release(w)
            elif w.active:
                fills.extend(self._match_resting(w, update))
            elif ts >= w.active_at:
                fills.extend(self._activate(w, update))
        return fills

    def open_orders(self) -> list[OrderRequest]:
        """Working orders, each with `size` set to its unfilled remainder."""
        return [replace(w.order, size=w.remaining) for w in self._working.values()]

    def credit(self, amount: float) -> None:
        """Add settlement proceeds (e.g. $1/share for a winning outcome)."""
        self._cash += amount

    # --- internals -----------------------------------------------------------
    def _admit(self, o: OrderRequest, now: float) -> str:
        if not (math.isfinite(o.price) and math.isfinite(o.size)):
            return "non-finite price or size"
        if o.size <= 0 or not 0.0 < o.price < 1.0:
            return f"invalid order: price={o.price} size={o.size}"
        if o.id in self._working:
            return f"duplicate order id {o.id}"
        w = _Working(order=o, remaining=o.size, active_at=now + self._latency_s)
        if o.side == Side.BUY:
            need = o.price * o.size + self._fee(o.price, o.size, False)
            free = self._cash - sum(x.reserved_cash for x in self._working.values())
            if need > free + _EPS:
                return f"insufficient balance: need ${need:.2f}, free ${free:.2f}"
            w.reserved_cash = need
        else:
            pos = self._book.get(o.token_id)
            held = pos.size if pos is not None else 0.0
            available = held - self._reserved_shares.get(o.token_id, 0.0)
            if o.size > available + _EPS:
                return f"insufficient inventory: sell {o.size} > available {available} (no shorts)"
            self._reserved_shares[o.token_id] = self._reserved_shares.get(o.token_id, 0.0) + o.size
        self._working[o.id] = w
        return ""

    def _activate(self, w: _Working, book: BookUpdate | None) -> list[Fill]:
        """First contact with the market: take what crosses, then rest or cancel the rest."""
        o = w.order
        immediate = o.time_in_force in (TimeInForce.FOK, TimeInForce.FAK) or (
            o.order_type == OrderType.MARKET
        )
        if book is None:
            if immediate:
                self._release(w)
                self._reject(o, "no book to take liquidity from")
                return []
            w.active = True
            return []
        levels = self._takeable(o, book)
        if o.time_in_force == TimeInForce.FOK and sum(q for _, q in levels) + _EPS < o.size:
            self._release(w)
            self._reject(o, "FOK not fully fillable")
            return []
        fills = []
        for price, qty in levels:
            take = min(qty, w.remaining)
            if take <= _EPS:
                break
            consumed = self._consumed.setdefault(o.token_id, {})
            consumed[price] = consumed.get(price, 0.0) + take
            fills.append(self._fill(w, price, take, is_maker=False))
        if w.remaining > _EPS and not immediate:
            w.active = True
        elif o.id in self._working:
            self._release(w)
        return fills

    def _takeable(self, o: OrderRequest, book: BookUpdate) -> list[tuple[float, float]]:
        """Opposite-side levels this order can take at, net of liquidity already consumed."""
        if o.side == Side.BUY:
            levels = sorted(book.asks) or ([(book.best_ask, math.inf)] if book.best_ask > 0 else [])
            crosses = [(p, q) for p, q in levels if p <= o.price + _EPS]
        else:
            levels = sorted(book.bids, reverse=True) or (
                [(book.best_bid, math.inf)] if book.best_bid > 0 else []
            )
            crosses = [(p, q) for p, q in levels if p >= o.price - _EPS]
        consumed = self._consumed.get(o.token_id, {})
        return [(p, q - consumed.get(p, 0.0)) for p, q in crosses if q - consumed.get(p, 0.0) > _EPS]

    def _match_resting(self, w: _Working, book: BookUpdate) -> list[Fill]:
        """A resting order fills once the opposite side reaches its price: the whole queue
        at its level has traded. Strictly through → full fill; at the level → up to the
        size resting there (all of it if depth is unknown)."""
        o = w.order
        if o.side == Side.BUY:
            touch, through = book.best_ask, book.best_ask > 0 and book.best_ask < o.price - _EPS
            reached = book.best_ask > 0 and book.best_ask <= o.price + _EPS
            same_level = book.asks
        else:
            touch, through = book.best_bid, book.best_bid > 0 and book.best_bid > o.price + _EPS
            reached = book.best_bid > 0 and book.best_bid >= o.price - _EPS
            same_level = book.bids
        if not reached:
            return []
        if through or not same_level:
            qty = w.remaining
        else:
            qty = min(w.remaining, sum(q for p, q in same_level if abs(p - touch) <= _EPS))
        if qty <= _EPS:
            return []
        fill = self._fill(w, o.price, qty, is_maker=True)
        if w.remaining <= _EPS:
            self._release(w)
        return [fill]

    def _fill(self, w: _Working, price: float, qty: float, *, is_maker: bool) -> Fill:
        o = w.order
        fee = self._fee(price, qty, is_maker)
        if o.side == Side.BUY:
            per_share = w.reserved_cash / w.remaining if w.remaining > _EPS else 0.0
            w.reserved_cash = max(0.0, w.reserved_cash - per_share * qty)
            self._cash -= price * qty + fee
        else:
            self._reserved_shares[o.token_id] = max(
                0.0, self._reserved_shares.get(o.token_id, 0.0) - qty
            )
            self._cash += price * qty - fee
        w.remaining -= qty
        return Fill(
            order_id=o.id,
            token_id=o.token_id,
            market_id=o.market_id,
            side=o.side,
            price=price,
            size=qty,
            fee=fee,
            ts=self._clock.now(),
            venue_order_id=o.id,
            strategy=o.strategy,
            asset=o.asset,
            direction=o.direction,
            fill_source="paper_maker" if is_maker else "paper_taker",
        )

    def _release(self, w: _Working) -> None:
        self._working.pop(w.order.id, None)
        if w.order.side == Side.SELL:
            token = w.order.token_id
            self._reserved_shares[token] = max(
                0.0, self._reserved_shares.get(token, 0.0) - w.remaining
            )
        w.reserved_cash = 0.0

    def _reject(self, o: OrderRequest, reason: str) -> None:
        self.rejections.append((o, reason))
        logger.warning("paper.order_rejected", order_id=o.id, token_id=o.token_id, reason=reason)
