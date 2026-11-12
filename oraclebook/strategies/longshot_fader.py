"""LongshotFader — bid for the favourite early in an interval market and hold to settlement.

Thesis: in short interval markets, takers pay up for the cheap side, so the favourite trades a
little under its win rate. The gap sits inside the taker fee, so a taker can't close it — only
a zero-fee resting bid can collect it. The strategy joins the favourite's best bid while the
favourite is priced inside a band and the interval is still early, one short-lived GTD quote
at a time, and never sells: a fill is held to settlement.

**Honest caveat: the edge did not replicate.** The defaults were fitted on one week of settled
Polymarket BTC up/down markets (5m and 15m, 2026-11-05..11) and tested once on the week before
it (2026-10-29..11-04), simulating fills only when the price traded through the bid:

    BTC   fitted week  +2.1c/share (t 2.4, 2,512 fills)   held-out week  +0.0c (t 0.0, 2,487 fills)
    ETH   fitted week  -0.9c                               held-out week  -0.3c
    SOL   fitted week  -4.2c                               held-out week  -3.8c
    XRP   fitted week  -4.6c                               held-out week  -5.1c

(per share held to settlement, including the venue's published 20% maker rebate, about 0.3c;
t-stats clustered by hour). The fitted result is consistent with what a search over price bands
and time windows turns up by chance: out of sample BTC is flat, and the thin markets lose to
adverse selection in both weeks.
It ships as the best-supported candidate that search found and as a worked example of a
time-aware GTD maker — **not** as a profitable strategy. Each fill wins a little about 74% of
the time and loses the stake otherwise (per-share s.d. 0.44), so a few weeks of forward paper
trading are needed before the sign of the edge is known. The reference live executor does not
report resting fills yet, so this cannot run live as shipped.

Needs market timing, so it only quotes markets announced by a `MarketsDiscovered` event that
carry both `start_ts` and `end_ts`.
"""
from __future__ import annotations

from dataclasses import dataclass

from oraclebook.core.events import BookUpdate, MarketsDiscovered, OrderFilled
from oraclebook.core.models import Market, OrderRequest, OrderType, Side, TimeInForce
from oraclebook.strategies.base import BaseStrategy

_EPS = 1e-9


@dataclass
class _Quote:
    order_id: str
    expires: int
    remaining: float


class LongshotFader(BaseStrategy):
    """Rests a GTD bid on the favourite of an interval market, early in the interval."""

    name = "longshot_fader"

    def __init__(
        self,
        size: float = 10.0,
        min_price: float = 0.70,
        max_price: float = 0.95,
        min_elapsed: float = 0.10,
        max_elapsed: float = 0.50,
        quote_ttl: float = 30.0,
        max_position: float | None = None,
    ) -> None:
        if not 0.5 < min_price <= max_price < 1.0:
            raise ValueError("need 0.5 < min_price <= max_price < 1: the band is the favourite")
        if not 0.0 <= min_elapsed < max_elapsed <= 1.0:
            raise ValueError("need 0 <= min_elapsed < max_elapsed <= 1")
        self.size = size
        self.min_price = min_price
        self.max_price = max_price
        self.min_elapsed = min_elapsed
        self.max_elapsed = max_elapsed
        self.quote_ttl = quote_ttl
        self.max_position = size if max_position is None else max_position  # shares per market
        self._markets: dict[str, Market] = {}
        self._quotes: dict[str, _Quote] = {}  # market_id -> the one working quote
        self._held: dict[str, float] = {}     # market_id -> shares bought

    async def on_markets(self, event: MarketsDiscovered) -> None:
        for market_id in [m for m, market in self._markets.items() if market.end_ts < event.ts]:
            del self._markets[market_id]
            self._quotes.pop(market_id, None)
            self._held.pop(market_id, None)
        for market in event.markets:
            if market.start_ts is not None and market.end_ts is not None:
                self._markets[market.market_id] = market

    async def on_book(self, book: BookUpdate) -> list[OrderRequest]:
        market = self._markets.get(book.market_id)
        if market is None:
            return []
        span = market.end_ts - market.start_ts
        elapsed = (book.ts - market.start_ts) / span
        if not self.min_elapsed <= elapsed < self.max_elapsed:
            return []
        # min_price > 0.5, so at most one outcome's bid can sit in the band: the favourite's.
        if not self.min_price - _EPS <= book.best_bid <= self.max_price + _EPS:
            return []
        if book.best_ask <= book.best_bid:
            return []  # one-sided or crossed book: no touch to join
        quote = self._quotes.get(book.market_id)
        if quote is not None and book.ts < quote.expires:
            return []
        size = min(self.size, self.max_position - self._held.get(book.market_id, 0.0))
        if size <= _EPS or size < market.min_order_size:
            return []
        closes = market.start_ts + self.max_elapsed * span
        expires = int(min(book.ts + self.quote_ttl, closes))
        if expires <= book.ts:
            return []
        order = OrderRequest(
            token_id=book.token_id, market_id=book.market_id, side=Side.BUY,
            price=book.best_bid, size=size, order_type=OrderType.LIMIT, strategy=self.name,
            time_in_force=TimeInForce.GTD, expiration=expires, book_age_ms=book.age_ms,
            reason=f"favourite_bid={book.best_bid:.2f} elapsed={elapsed:.2f}",
        )
        self._quotes[book.market_id] = _Quote(order.id, expires, size)
        return [order]

    async def on_fill(self, event: OrderFilled) -> None:
        for fill in event.fills:
            if fill.strategy != self.name or fill.side != Side.BUY:
                continue
            self._held[fill.market_id] = self._held.get(fill.market_id, 0.0) + fill.size
            quote = self._quotes.get(fill.market_id)
            if quote is not None and quote.order_id == fill.order_id:
                quote.remaining -= fill.size
                if quote.remaining <= _EPS:
                    del self._quotes[fill.market_id]
