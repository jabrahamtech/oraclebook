"""Replay: record real order flow, then backtest on it — reproducible for a single feed
(paper fills; cross-topic interleaving best-effort). The headline feature."""
import asyncio
import os
import tempfile

import oraclebook as ob
from oraclebook.core.bus import Bus
from oraclebook.core.events import EVT_BOOK_UPDATE, BookUpdate
from oraclebook.replay import Recorder, run_replay


class CheapBuyer(ob.BaseStrategy):
    name = "cb"

    async def on_book(self, book):
        if book.best_bid <= 0.40:
            return [ob.OrderRequest(token_id=book.token_id, market_id=book.market_id, side=ob.Side.BUY,
                                    price=book.best_bid, size=10, order_type=ob.OrderType.LIMIT,
                                    strategy=self.name)]
        return []


async def _record(path):
    bus = Bus()
    rec = Recorder(bus, path)
    await rec.start()
    for bid in [0.50, 0.38, 0.42, 0.35, 0.60]:  # two are <= 0.40
        await bus.publish(EVT_BOOK_UPDATE, BookUpdate(token_id="UP", market_id="m1", best_bid=bid))
        await asyncio.sleep(0.001)
    await asyncio.sleep(0.03)
    await rec.stop()


async def test_record_then_replay_is_reproducible():
    path = os.path.join(tempfile.mkdtemp(), "rec.jsonl")
    await _record(path)
    app1 = await run_replay(path, CheapBuyer(), starting_balance=100.0)
    app2 = await run_replay(path, CheapBuyer(), starting_balance=100.0)
    prices1 = sorted(f["payload"]["price"] for f in app1.ledger.fills())
    prices2 = sorted(f["payload"]["price"] for f in app2.ledger.fills())
    assert prices1 == [0.35, 0.38]      # only the cheap ticks were bought
    assert prices1 == prices2           # same recording -> identical result
