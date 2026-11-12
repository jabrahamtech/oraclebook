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


class FlakyExecutor(ob.PaperExecutor):
    """Raises on the first submit, then behaves."""

    def __init__(self, book):
        super().__init__(book, starting_balance=100.0)
        self.calls = 0

    async def submit(self, orders):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("venue 503")
        return await super().submit(orders)


async def test_executor_error_does_not_kill_the_consumer():
    book = ob.PositionTracker()
    app = ob.App(strategy=Buyer(), executor=FlakyExecutor(book), book=book)
    await app.start()
    await app.bus.publish(EVT_BOOK_UPDATE, BookUpdate(token_id="UP", market_id="m1", best_bid=0.5))
    await asyncio.sleep(0.05)
    await app.bus.publish(EVT_BOOK_UPDATE, BookUpdate(token_id="UP", market_id="m1", best_bid=0.5))
    await asyncio.sleep(0.05)
    await app.stop()
    assert app.executor.calls == 2
    assert app.book.get("UP").size == 10


class SlowExecutor(ob.PaperExecutor):
    async def submit(self, orders):
        await asyncio.sleep(0.05)
        return await super().submit(orders)


async def test_stop_waits_for_in_flight_submit():
    book = ob.PositionTracker()
    app = ob.App(strategy=Buyer(), executor=SlowExecutor(book), book=book)
    await app.start()
    await app.bus.publish(EVT_BOOK_UPDATE, BookUpdate(token_id="UP", market_id="m1", best_bid=0.5))
    await asyncio.sleep(0.01)
    await app.stop()
    assert app.book.get("UP").size == 10


async def test_resolution_is_written_to_the_ledger(tmp_path):
    from oraclebook.core.events import EVT_POSITION_RESOLVED, PositionResolved

    ledger = ob.Ledger(str(tmp_path / "events.jsonl"))
    app = ob.App(strategy=Buyer(), ledger=ledger)
    await app.start()
    res = ob.Resolution(token_id="UP", market_id="m1", strategy="buyer", direction="up",
                        settlement_value=1.0, pnl=4.5)
    await app.bus.publish(EVT_POSITION_RESOLVED, PositionResolved(resolution=res))
    await asyncio.sleep(0.05)
    await app.stop()
    assert [r["payload"]["pnl"] for r in ledger.resolutions()] == [4.5]


async def test_strategy_is_told_about_discovered_markets():
    from oraclebook.core.events import EVT_MARKETS_DISCOVERED, MarketsDiscovered

    class Listener(ob.BaseStrategy):
        seen = ()

        async def on_markets(self, event):
            self.seen = event.markets

    s = Listener()
    app = ob.App(strategy=s, executor="paper")
    await app.start()
    market = ob.Market(venue="v", market_id="m1", condition_id="c", token_ids=("UP", "DN"))
    await app.bus.publish(EVT_MARKETS_DISCOVERED, MarketsDiscovered(markets=(market,)))
    await asyncio.sleep(0.05)
    await app.stop()
    assert s.seen == (market,)
