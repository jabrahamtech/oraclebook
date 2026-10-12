"""CompleteSetMaker — the reference strategy, and an honest lesson.

It quotes every leg of an outcome market: when ``1 - sum(best bids)`` clears the edge floor,
it posts a BUY on *each* outcome at its best bid, aiming to buy a complete set for under $1
and redeem it for exactly $1 at settlement. It only quotes a full, fresh, non-crossing set:
every leg of the market must have a live bid below its best ask, and no leg may be older than
``max_leg_age_s``. With ``quote_ttl_s`` set, quotes are GTD and the market is re-quoted only
after they expire; ``unhedged(market_id)`` reports the filled-size imbalance between legs.

**Honest caveat.** This naive form *loses money live.* Posting both legs but getting filled
on only the adverse one (one-leg adverse selection) is the dominant outcome, and an offline
both-leg-lock estimate badly overstates the live lock rate. Paper fills now require
trade-through (a resting bid fills only once the ask reaches its price), which is more
conservative than filling at the bid, but it still cannot model queue position or who you
are trading against. It ships as a faithful, instructive example of why complete-set
market-making is harder than it looks — **not** as a profitable strategy. Validate any
variant in paper and Replay (and ideally measure live fill realism) before risking capital.

Prior art: the complete-set arbitrage concept is documented in polybot
(https://github.com/ent0n29/polybot, MIT); this implementation is an independent
re-implementation of the concept on oraclebook's own primitives.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from oraclebook.core.events import BookUpdate, OrderFilled
from oraclebook.core.models import OrderRequest, OrderType, Side, TimeInForce
from oraclebook.strategies.base import BaseStrategy

_EPS = 1e-9


@dataclass(frozen=True)
class _Leg:
    bid: float
    ask: float      # 0 = no ask
    ts: float       # when this leg's book arrived (BookUpdate.ts)
    age_ms: float   # how old the book already was on arrival


class CompleteSetMaker(BaseStrategy):
    """Posts a BUY on every leg of a market when the complete-set edge clears the floor."""

    name = "complete_set_maker"

    def __init__(
        self,
        min_edge: float = 0.02,
        size: float = 10.0,
        *,
        legs_per_market: int = 2,
        markets: dict[str, tuple[str, ...]] | None = None,
        max_leg_age_s: float = 2.0,
        quote_ttl_s: float = 0.0,
    ) -> None:
        self.min_edge = min_edge
        self.size = size
        self.legs_per_market = legs_per_market
        self.markets = markets                      # market_id -> its full set of token ids
        self.max_leg_age_s = max_leg_age_s
        self.quote_ttl_s = quote_ttl_s              # 0 = GTC, quote each market once
        self._legs: dict[str, dict[str, _Leg]] = {}
        self._quoted_until: dict[str, float] = {}   # market_id -> when its quotes expire
        self._filled: dict[str, dict[str, float]] = {}  # market_id -> {token_id: net filled}

    async def on_book(self, book: BookUpdate) -> list[OrderRequest]:
        if not book.market_id:
            return []
        expected = self._expected_tokens(book.market_id)
        if expected is not None and book.token_id not in expected:
            return []
        legs = self._legs.setdefault(book.market_id, {})
        if not book.best_bid > 0:
            legs.pop(book.token_id, None)  # bid side gone: never quote off a remembered bid
            return []
        legs[book.token_id] = _Leg(book.best_bid, book.best_ask, book.ts, book.age_ms)

        if book.ts < self._quoted_until.get(book.market_id, -math.inf):
            return []  # previous quotes still live
        if expected is not None:
            if set(legs) != expected:
                return []
        elif len(legs) != self.legs_per_market:
            return []
        if any(leg.ask > 0 and not leg.bid < leg.ask for leg in legs.values()):
            return []  # a bid at/through the ask would take liquidity, not make it
        age_ms = max((book.ts - leg.ts) * 1000.0 + leg.age_ms for leg in legs.values())
        if not age_ms <= self.max_leg_age_s * 1000.0:
            return []
        edge = round(1.0 - sum(leg.bid for leg in legs.values()), 9)
        if edge + _EPS < self.min_edge:
            return []

        if self.quote_ttl_s > 0:
            tif, expiration = TimeInForce.GTD, math.ceil(book.ts + self.quote_ttl_s)
            self._quoted_until[book.market_id] = expiration
        else:
            tif, expiration = TimeInForce.GTC, 0
            self._quoted_until[book.market_id] = math.inf
        return [
            OrderRequest(
                token_id=token, market_id=book.market_id, side=Side.BUY, price=leg.bid,
                size=self.size, order_type=OrderType.LIMIT, strategy=self.name,
                time_in_force=tif, expiration=expiration, book_age_ms=age_ms,
                reason=f"complete_set_edge={edge:.3f}",
            )
            for token, leg in legs.items()
        ]

    async def on_fill(self, event: OrderFilled) -> None:
        for f in event.fills:
            if f.strategy != self.name:
                continue
            filled = self._filled.setdefault(f.market_id, {})
            signed = f.size if f.side == Side.BUY else -f.size
            filled[f.token_id] = filled.get(f.token_id, 0.0) + signed

    def unhedged(self, market_id: str) -> float:
        """Filled-size imbalance between the market's legs (|YES - NO| for a binary): the
        shares bought that are not part of a complete set."""
        filled = self._filled.get(market_id, {})
        expected = self._expected_tokens(market_id)
        n_legs = len(expected) if expected is not None else self.legs_per_market
        sizes = list(filled.values()) + [0.0] * max(0, n_legs - len(filled))
        return max(sizes) - min(sizes) if sizes else 0.0

    def _expected_tokens(self, market_id: str) -> set[str] | None:
        if self.markets is None:
            return None
        return set(self.markets.get(market_id, ()))
