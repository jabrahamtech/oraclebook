"""LongshotFader: one GTD bid on the favourite, inside its price band and elapsed window."""
import os
import tempfile

import pytest

import oraclebook as ob
from oraclebook.core.bus import Bus
from oraclebook.core.events import (
    EVT_BOOK_UPDATE,
    EVT_MARKETS_DISCOVERED,
    BookUpdate,
    MarketsDiscovered,
    OrderFilled,
)
from oraclebook.replay import Recorder, run_replay

MARKET = ob.Market(venue="v", market_id="m1", condition_id="c", token_ids=("UP", "DN"),
                   start_ts=1000.0, end_ts=1300.0)  # a 5-minute interval


async def _strategy(**kwargs):
    s = ob.LongshotFader(**kwargs)
    await s.on_markets(MarketsDiscovered(markets=(MARKET,), ts=900.0))
    return s


def _book(ts, bid, token="UP", market="m1", ask=None, depth=False):
    ask = round(bid + 0.01, 2) if ask is None else ask
    levels = {"bids": ((bid, 100.0),), "asks": ((ask, 100.0),)} if depth else {}
    return BookUpdate(token_id=token, market_id=market, best_bid=bid, best_ask=ask, ts=ts, **levels)


def _filled(order, size):
    fill = ob.Fill(order_id=order.id, token_id=order.token_id, market_id=order.market_id,
                   side=order.side, price=order.price, size=size, strategy=order.strategy)
    return OrderFilled(fills=(fill,))


async def test_bids_the_favourite_inside_band_and_window():
    s = await _strategy(size=10.0, quote_ttl=30.0)
    (order,) = await s.on_book(_book(1060.0, 0.80))                # 20% elapsed
    assert (order.token_id, order.side, order.price, order.size) == ("UP", ob.Side.BUY, 0.80, 10.0)
    assert order.order_type is ob.OrderType.LIMIT and order.time_in_force is ob.TimeInForce.GTD
    assert order.expiration == 1090


async def test_quiet_outside_the_elapsed_window():
    s = await _strategy()
    assert await s.on_book(_book(1010.0, 0.80)) == []              # 3% elapsed: too early
    assert await s.on_book(_book(1150.0, 0.80)) == []              # 50% elapsed: window closed
    assert await s.on_book(_book(990.0, 0.80)) == []               # before the interval opens


async def test_quiet_outside_the_price_band():
    s = await _strategy()
    assert await s.on_book(_book(1060.0, 0.55)) == []              # no clear favourite yet
    assert await s.on_book(_book(1060.0, 0.97)) == []              # too little left to win
    assert await s.on_book(_book(1060.0, 0.19, token="DN")) == []  # the longshot is never bid
    assert await s.on_book(_book(1060.0, 0.80, ask=0.0)) == []     # no ask: not a two-sided book


async def test_one_working_quote_then_requotes_after_it_expires():
    s = await _strategy(quote_ttl=30.0)
    assert len(await s.on_book(_book(1060.0, 0.80))) == 1
    assert await s.on_book(_book(1075.0, 0.82)) == []              # first quote still working
    (order,) = await s.on_book(_book(1090.0, 0.82))                # expired: quote the new bid
    assert order.price == 0.82


async def test_quote_never_outlives_the_window():
    s = await _strategy(quote_ttl=30.0)
    (order,) = await s.on_book(_book(1140.0, 0.80))
    assert order.expiration == 1150                                # window closes at 50% elapsed


async def test_holds_at_most_max_position_per_market():
    s = await _strategy(size=10.0, max_position=15.0)
    (first,) = await s.on_book(_book(1060.0, 0.80))
    await s.on_fill(_filled(first, 10.0))
    (second,) = await s.on_book(_book(1061.0, 0.80))               # filled quote frees the slot
    assert second.size == 5.0                                      # only the room left
    await s.on_fill(_filled(second, 5.0))
    assert await s.on_book(_book(1100.0, 0.80)) == []


async def test_partial_fill_keeps_the_quote_working():
    s = await _strategy(size=10.0)
    (order,) = await s.on_book(_book(1060.0, 0.80))
    await s.on_fill(_filled(order, 4.0))
    assert await s.on_book(_book(1061.0, 0.80)) == []
    (again,) = await s.on_book(_book(1090.0, 0.80))
    assert again.size == 6.0


async def test_ignores_markets_without_timing():
    s = await _strategy()
    untimed = ob.Market(venue="v", market_id="m2", condition_id="c", token_ids=("A", "B"))
    await s.on_markets(MarketsDiscovered(markets=(untimed,), ts=950.0))
    assert await s.on_book(_book(1060.0, 0.80, market="m2")) == []
    assert await s.on_book(_book(1060.0, 0.80, market="never-announced")) == []


async def test_forgets_markets_that_have_ended():
    s = await _strategy()
    await s.on_markets(MarketsDiscovered(markets=(), ts=1400.0))
    assert await s.on_book(_book(1060.0, 0.80)) == []


def test_band_must_define_a_favourite():
    with pytest.raises(ValueError):
        ob.LongshotFader(min_price=0.45)
    with pytest.raises(ValueError):
        ob.LongshotFader(min_elapsed=0.6, max_elapsed=0.5)


async def test_replay_fills_the_bid_only_when_price_trades_through():
    path = os.path.join(tempfile.mkdtemp(), "rec.jsonl")
    bus = Bus()
    rec = Recorder(bus, path)
    await rec.start()
    await bus.publish(EVT_MARKETS_DISCOVERED, MarketsDiscovered(markets=(MARKET,), ts=900.0))
    for ts, bid in [(1060.0, 0.80), (1065.0, 0.81), (1070.0, 0.78), (1200.0, 0.90)]:
        await bus.publish(EVT_BOOK_UPDATE, _book(ts, bid, depth=True))
    await rec.stop()

    app = await run_replay(path, ob.LongshotFader(size=10.0), fill_model="depth")
    (fill,) = [f["payload"] for f in app.ledger.fills()]
    # bid 0.80 rested through 0.81 and filled as a maker when the ask came down to 0.79
    assert (fill["price"], fill["size"], fill["meta"]["liquidity"]) == (0.80, 10.0, "maker")
    assert fill["ts"] == 1070.0
