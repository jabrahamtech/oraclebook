"""CompleteSetMaker — the reference strategy, and an honest lesson.

It quotes both legs of a binary up/down market: when ``1 - (yes_bid + no_bid)`` clears the
edge floor, it posts a BUY on *each* outcome at its best bid, aiming to buy a complete set
for under $1 and redeem it for exactly $1 at settlement.

**Honest caveat.** This naive form *loses money live.* Posting both legs but getting filled
on only the adverse one (one-leg adverse
selection) is the dominant outcome, and an offline both-leg-lock estimate badly overstates
the live lock rate. It ships as a faithful, instructive example of why complete-set
market-making is harder than it looks — **not** as a profitable strategy. Validate any
variant in paper and Replay (and ideally measure live fill realism) before risking capital.

Prior art: the complete-set arbitrage concept is documented in polybot
(https://github.com/ent0n29/polybot, MIT); this implementation is an independent
re-implementation of the concept on oraclebook's own primitives.
"""
from __future__ import annotations

from oraclebook.core.events import BookUpdate
from oraclebook.core.models import OrderRequest, OrderType, Side, TimeInForce
from oraclebook.strategies.base import BaseStrategy


class CompleteSetMaker(BaseStrategy):
    """Posts both-leg bids on a binary market when the complete-set edge clears the floor."""

    name = "complete_set_maker"

    def __init__(self, min_edge: float = 0.02, size: float = 10.0) -> None:
        self.min_edge = min_edge
        self.size = size
        self._bids: dict[str, dict[str, float]] = {}  # market_id -> {token_id: best_bid}
        self._quoted: set[str] = set()                # quote each market once (reference)

    async def on_book(self, book: BookUpdate) -> list[OrderRequest]:
        if not book.market_id or book.best_bid <= 0:
            return []
        legs = self._bids.setdefault(book.market_id, {})
        legs[book.token_id] = book.best_bid
        if len(legs) < 2 or book.market_id in self._quoted:
            return []  # need both legs, and only quote a given market once
        edge = 1.0 - sum(legs.values())
        if edge < self.min_edge:
            return []
        self._quoted.add(book.market_id)
        return [
            OrderRequest(
                token_id=token, market_id=book.market_id, side=Side.BUY, price=bid,
                size=self.size, order_type=OrderType.LIMIT, strategy=self.name,
                time_in_force=TimeInForce.GTC, book_age_ms=book.age_ms,
                reason=f"complete_set_edge={edge:.3f}",
            )
            for token, bid in legs.items()
        ]
