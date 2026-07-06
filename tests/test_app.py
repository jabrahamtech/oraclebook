"""App: the full loop (book -> strategy -> gates -> paper fill -> book + on_fill) and
the gate-rejection path."""
import asyncio

import oraclebook as ob
from oraclebook.core.events import EVT_BOOK_UPDATE, BookUpdate


class Buyer(ob.BaseStrategy):
    name = "buyer"

    def __init__(self):
        self.fills_seen = 0

    async def on_book(self, book):
        return [ob.OrderRequest(token_id=book.token_id, market_id=book.market_id, side=ob.Side.BUY,
                                price=book.best_bid, size=10, order_type=ob.OrderType.LIMIT,
                                strategy=self.name)]

    async def on_fill(self, event):
        self.fills_seen += len(event.fills)


async def test_end_to_end_fill_updates_book_balance_and_notifies():
    s = Buyer()
    app = ob.App(strategy=s, executor="paper", starting_balance=100.0, gates=[ob.OrderSizeGate(1.0)])
    await app.start()
    await app.bus.publish(EVT_BOOK_UPDATE, BookUpdate(token_id="UP", market_id="m1", best_bid=0.55))
    await asyncio.sleep(0.1)
    await app.stop()
    p = app.book.get("UP")
    assert p is not None and p.size == 10
    assert abs(await app.executor.get_balance() - 94.5) < 1e-9
    assert s.fills_seen == 1


async def test_gate_rejection_blocks_the_fill():
    app = ob.App(strategy=Buyer(), executor="paper", starting_balance=3.0, gates=[ob.BalanceGate()])
    await app.start()
    await app.bus.publish(EVT_BOOK_UPDATE, BookUpdate(token_id="UP", market_id="m1", best_bid=0.55))
    await asyncio.sleep(0.1)
    await app.stop()
    assert app.book.get("UP") is None  # $5.50 > $3.00 -> rejected
