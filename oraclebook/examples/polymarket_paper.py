"""Paper-trade CompleteSetMaker against LIVE Polymarket data — no credentials, no risk.

    python -m oraclebook.examples.polymarket_paper

Discovers the active BTC 5-minute up/down markets, streams their real order books, runs the
CompleteSetMaker in PAPER mode (simulated fills), and prints positions + balance. This is the
full integration: a real Strategy + the live Polymarket Venue + the paper Executor.

Requires the optional dep: `pip install oraclebook[polymarket]`. See the CompleteSetMaker
docstring for why this strategy is instructive rather than profitable.
"""
from __future__ import annotations

import asyncio

from oraclebook import App, CompleteSetMaker, OrderSizeGate
from oraclebook.adapters.polymarket import PolymarketVenue


async def main(run_seconds: float = 20.0) -> None:
    app = App(
        venue=PolymarketVenue(assets=["BTC"], intervals=[5]),
        strategy=CompleteSetMaker(min_edge=0.02, size=10.0),
        executor="paper",
        gates=[OrderSizeGate(min_notional_usd=1.0)],
        starting_balance=500.0,
    )
    await app.start()
    await asyncio.sleep(run_seconds)
    await app.stop()

    print(f"\n=== CompleteSetMaker — paper vs LIVE Polymarket ({run_seconds:.0f}s) ===")
    print(f"balance: ${await app.executor.get_balance():.2f}  (started $500.00)")
    positions = app.book.positions()
    for p in positions:
        print(f"  {p.token_id[:18]} {p.size:.0f} @ {p.avg_entry:.3f}")
    if not positions:
        print("  (no complete-set edge cleared the floor this run — expected; edges are rare)")


if __name__ == "__main__":
    asyncio.run(main())
