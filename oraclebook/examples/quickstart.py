"""Runnable quickstart — `python -m oraclebook.examples.quickstart`.

Runs the full oraclebook loop against the synthetic MockVenue (no network, no keys):
a tiny strategy buys whichever outcome's bid drops cheap, orders pass the gate chain,
the PaperExecutor fills them, and the Book + balance update — then we print the result.
This is the smallest end-to-end demonstration of the framework.
"""
from __future__ import annotations

import asyncio

from oraclebook import App, BaseStrategy, OrderRequest, OrderSizeGate, OrderType, Side
from oraclebook.examples.mock_venue import MockVenue


class CheapBuyer(BaseStrategy):
    """Buys any outcome whose best bid falls to/below 0.40 — a stand-in 'edge'."""

    name = "cheap_buyer"

    async def on_book(self, book) -> list[OrderRequest]:
        if book.best_bid <= 0.40:
            return [
                OrderRequest(
                    token_id=book.token_id, market_id=book.market_id, side=Side.BUY,
                    price=book.best_bid, size=10, order_type=OrderType.LIMIT, strategy=self.name,
                )
            ]
        return []


async def main(run_seconds: float = 2.0) -> None:
    app = App(
        venue=MockVenue(),
        strategy=CheapBuyer(),
        executor="paper",
        gates=[OrderSizeGate(min_notional_usd=1.0)],
        starting_balance=100.0,
    )
    await app.start()
    await asyncio.sleep(run_seconds)
    await app.stop()

    bal = await app.executor.get_balance()
    print(f"\n=== oraclebook quickstart result (paper, {run_seconds:.0f}s on MockVenue) ===")
    print(f"balance: ${bal:.2f}  (started $100.00)")
    for p in app.book.positions():
        print(f"  position {p.token_id}: {p.size:.0f} @ {p.avg_entry:.3f}")
    if not app.book.positions():
        print("  (no fills this run — the random walk stayed above the buy threshold)")


if __name__ == "__main__":
    asyncio.run(main())
