"""DepthPaperExecutor: fills walk the book, pay latency, and fill partially."""
import asyncio
import json
import os
import tempfile

import pytest

import oraclebook as ob
from oraclebook.adapters.polymarket.feed import PolymarketFeed
from oraclebook.adapters.polymarket.fees import fee_amount
from oraclebook.core.bus import Bus
from oraclebook.core.depth_paper_executor import DepthPaperExecutor
from oraclebook.core.events import EVT_BOOK_UPDATE, BookUpdate
from oraclebook.replay import Recorder, run_replay


def _book(ts, bids=(), asks=()):
    return BookUpdate(
        token_id="T", market_id="m", ts=ts, bids=tuple(bids), asks=tuple(asks),
        best_bid=bids[0][0] if bids else 0.0, best_ask=asks[0][0] if asks else 0.0,
    )


def _order(side, price, size, tif=ob.TimeInForce.FAK, expiration=0):
    return ob.OrderRequest(
        token_id="T", market_id="m", side=side, price=price, size=size,
        order_type=ob.OrderType.LIMIT, strategy="s", time_in_force=tif, expiration=expiration,
    )


def _executor(latency_ms=0.0, fee=None):
    clock = ob.ReplayClock(strict=False)
    ex = DepthPaperExecutor(ob.PositionTracker(), 1000.0, latency_ms=latency_ms, fee=fee,
                            clock=clock)
    return ex, clock


def _hold(ex, size, price=0.30):
    ex._book.on_fill(ob.Fill(order_id="h", token_id="T", market_id="m", side=ob.Side.BUY,
                             price=price, size=size))


async def test_buy_walks_ask_levels_at_vwap():
    ex, _ = _executor()
    ex.on_book(_book(0, bids=[(0.48, 100)], asks=[(0.50, 100), (0.52, 100), (0.60, 100)]))
    [fill] = await ex.submit([_order(ob.Side.BUY, 0.55, 150)])
    assert fill.size == 150
    assert fill.price == pytest.approx((0.50 * 100 + 0.52 * 50) / 150)
    assert fill.meta["slippage"] == pytest.approx(fill.price - 0.50)
    assert fill.meta["levels"] == 2
    assert await ex.get_balance() == pytest.approx(1000 - (50 + 26))


async def test_taken_liquidity_is_gone_for_the_next_order():
    ex, _ = _executor()
    ex.on_book(_book(0, asks=[(0.50, 100)]))
    [first] = await ex.submit([_order(ob.Side.BUY, 0.50, 80)])
    [second] = await ex.submit([_order(ob.Side.BUY, 0.50, 80)])
    assert (first.size, second.size) == (80, 20)
    assert ex.unfilled[0].size == pytest.approx(60)
    assert ex.unfilled[0].reason == "no_depth"


async def test_fok_needs_full_size_fak_takes_what_is_there():
    ex, _ = _executor()
    _hold(ex, 100)
    ex.on_book(_book(0, bids=[(0.40, 30), (0.39, 20)]))
    assert await ex.submit([_order(ob.Side.SELL, 0.40, 50, ob.TimeInForce.FOK)]) == []
    assert ex.unfilled[-1].reason == "fok_insufficient_depth"
    [fill] = await ex.submit([_order(ob.Side.SELL, 0.40, 50, ob.TimeInForce.FAK)])
    assert fill.size == 30 and fill.price == pytest.approx(0.40)


async def test_latency_fills_against_the_book_at_arrival():
    ex, _ = _executor(latency_ms=2000)
    ex.on_book(_book(0, asks=[(0.50, 100)]))
    assert await ex.submit([_order(ob.Side.BUY, 0.60, 50)]) == []
    assert ex.on_book(_book(1, asks=[(0.55, 100)])) == []  # still in flight
    [fill] = ex.on_book(_book(3, asks=[(0.58, 100)]))       # arrived at t=2: book from t=1
    assert fill.price == pytest.approx(0.55)
    assert fill.ts == pytest.approx(2.0)
    assert fill.meta["ref_price"] == pytest.approx(0.50)
    assert fill.meta["slippage"] == pytest.approx(0.05)


async def test_resting_order_fills_only_on_trade_through_as_maker():
    ex, _ = _executor(fee=lambda p, s, maker: 0.0 if maker else 1.0)
    ex.on_book(_book(0, bids=[(0.44, 100)], asks=[(0.47, 100)]))
    assert await ex.submit([_order(ob.Side.BUY, 0.45, 50, ob.TimeInForce.GTC)]) == []
    assert ex.on_book(_book(1, bids=[(0.44, 100)], asks=[(0.46, 100)])) == []
    [fill] = ex.on_book(_book(2, bids=[(0.43, 100)], asks=[(0.44, 30), (0.45, 5)]))
    assert fill.size == 35 and fill.price == pytest.approx(0.45)
    assert fill.fee == 0.0 and fill.meta["liquidity"] == "maker"
    assert ex.pending == 1
    assert await ex.cancel_all() == 1
    assert ex.unfilled[-1].reason == "cancelled"


async def test_gtd_expires():
    ex, _ = _executor()
    ex.on_book(_book(0, asks=[(0.60, 10)]))
    await ex.submit([_order(ob.Side.BUY, 0.50, 10, ob.TimeInForce.GTD, expiration=5)])
    assert ex.on_book(_book(6, asks=[(0.49, 10)])) == []
    assert ex.unfilled[-1].reason == "expired"


async def test_top_of_book_only_update_means_depth_unknown():
    ex, _ = _executor()
    ex.on_book(_book(0, asks=[(0.50, 100)]))
    ex.on_book(BookUpdate(token_id="T", market_id="m", ts=1, best_bid=0.49, best_ask=0.51))
    assert await ex.submit([_order(ob.Side.BUY, 0.60, 10)]) == []
    assert ex.unfilled[-1].reason == "book_unknown"


async def test_fee_charged_per_level():
    ex, _ = _executor(fee=lambda p, s, maker: fee_amount(p, s, is_maker=maker))
    ex.on_book(_book(0, asks=[(0.50, 10), (0.90, 10)]))
    [fill] = await ex.submit([_order(ob.Side.BUY, 0.90, 20)])
    assert fill.fee == pytest.approx(fee_amount(0.50, 10) + fee_amount(0.90, 10))


class AskTaker(ob.BaseStrategy):
    """Buys 100 at the touch on every book where the ask is <= 0.50."""

    name = "taker"

    async def on_book(self, book):
        if 0 < book.best_ask <= 0.50:
            return [ob.OrderRequest(token_id=book.token_id, market_id=book.market_id,
                                    side=ob.Side.BUY, price=book.best_ask, size=100,
                                    order_type=ob.OrderType.LIMIT, strategy=self.name,
                                    time_in_force=ob.TimeInForce.FAK)]
        return []


async def _record(path):
    bus = Bus()
    rec = Recorder(bus, path)
    await rec.start()
    books = [
        _book(1.0, bids=[(0.48, 50)], asks=[(0.50, 40), (0.53, 200)]),
        _book(1.5, bids=[(0.50, 50)], asks=[(0.54, 40), (0.56, 200)]),
        _book(3.0, bids=[(0.50, 50)], asks=[(0.55, 40), (0.58, 200)]),
    ]
    for b in books:
        await bus.publish(EVT_BOOK_UPDATE, b)
        await asyncio.sleep(0.001)
    await asyncio.sleep(0.03)
    await rec.stop()


async def test_replay_depth_vs_instant():
    path = os.path.join(tempfile.mkdtemp(), "rec.jsonl")
    await _record(path)

    instant = await run_replay(path, AskTaker())
    [f] = [r["payload"] for r in instant.ledger.fills()]
    assert (f["price"], f["size"]) == (0.50, 100)  # the flattering model

    depth = await run_replay(path, AskTaker(), fill_model="depth")
    [f] = [r["payload"] for r in depth.ledger.fills()]
    assert (f["price"], f["size"]) == (0.50, 40)  # only 40 shown at 0.50; FAK drops the rest

    late = await run_replay(path, AskTaker(), fill_model="depth", latency_ms=1000)
    again = await run_replay(path, AskTaker(), fill_model="depth", latency_ms=1000)
    fills = [r["payload"] for r in late.ledger.fills()]
    assert fills == [r["payload"] for r in again.ledger.fills()]  # reproducible
    assert fills == []  # by arrival the ask is 0.54 > limit 0.50: missed
    assert [u.reason for u in late.executor.unfilled] == ["no_depth"]


def test_instant_rejects_depth_only_options():
    with pytest.raises(ValueError):
        asyncio.run(run_replay("unused.jsonl", AskTaker(), latency_ms=100))


async def test_polymarket_feed_applies_price_change_levels():
    bus = Bus()
    sub = bus.subscribe_raw(EVT_BOOK_UPDATE)
    feed = PolymarketFeed(bus, assets=[], intervals=[])
    snapshot = {"asset_id": "T", "bids": [{"price": "0.48", "size": "100"}],
                "asks": [{"price": "0.50", "size": "40"}, {"price": "0.52", "size": "60"}]}
    change = {"event_type": "price_change", "price_changes": [
        {"asset_id": "T", "price": "0.50", "size": "0", "side": "SELL",
         "best_bid": "0.48", "best_ask": "0.52"},
        {"asset_id": "T", "price": "0.49", "size": "25", "side": "BUY",
         "best_bid": "0.49", "best_ask": "0.52"},
    ]}
    await feed._on_message(json.dumps(snapshot))
    await feed._on_message(json.dumps(change))
    updates = [sub.get_nowait() for _ in range(sub.qsize())]
    assert len(updates) == 3
    last = updates[-1]
    assert last.asks == ((0.52, 60.0),)
    assert last.bids == ((0.49, 25.0), (0.48, 100.0))
    assert (last.best_bid, last.best_ask) == (0.49, 0.52)


async def test_sell_is_capped_by_inventory():
    ex, _ = _executor()
    ex.on_book(_book(0, bids=[(0.50, 100)]))
    assert await ex.submit([_order(ob.Side.SELL, 0.50, 10)]) == []          # nothing held
    assert ex.unfilled[-1].reason == "no_inventory"
    _hold(ex, 30)
    fills = await ex.submit([_order(ob.Side.SELL, 0.50, 20), _order(ob.Side.SELL, 0.50, 20)])
    assert [f.size for f in fills] == [20, 10]  # one batch can't sell the same shares twice
    assert await ex.get_balance() == pytest.approx(1000 + 15)


async def test_buys_cannot_spend_cash_they_do_not_have():
    clock = ob.ReplayClock(strict=False)
    ex = DepthPaperExecutor(ob.PositionTracker(), 60.0, clock=clock)
    ex.on_book(_book(0, asks=[(0.60, 1000)]))
    for _ in range(3):
        await ex.submit([_order(ob.Side.BUY, 0.50, 100, ob.TimeInForce.GTC)])
    assert await ex.get_balance() == pytest.approx(60 - 150)  # gates see the reservation
    fills = ex.on_book(_book(1, asks=[(0.50, 1000)]))
    assert sum(f.size for f in fills) == pytest.approx(120)
    assert ex._balance == pytest.approx(0.0)


async def test_gtd_expired_before_arrival_never_fills():
    ex, _ = _executor(latency_ms=5000)
    ex.on_book(_book(100, asks=[(0.50, 10)]))
    await ex.submit([_order(ob.Side.BUY, 0.60, 10, ob.TimeInForce.GTD, expiration=102)])
    assert ex.on_book(_book(106, asks=[(0.50, 10)])) == []
    assert ex.unfilled[-1].reason == "expired"


async def test_order_arriving_blind_takes_at_the_next_book_price():
    ex, _ = _executor(fee=lambda p, s, maker: 0.0 if maker else 1.0)
    ex.on_book(BookUpdate(token_id="T", market_id="m", ts=0, best_bid=0.49, best_ask=0.51))
    assert await ex.submit([_order(ob.Side.BUY, 0.90, 10, ob.TimeInForce.GTC)]) == []
    [fill] = ex.on_book(_book(1, asks=[(0.50, 100)]))
    assert fill.price == pytest.approx(0.50)
    assert fill.meta["liquidity"] == "taker" and fill.fee == pytest.approx(1.0)
