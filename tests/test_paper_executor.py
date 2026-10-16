"""PaperExecutor: conservative book-aware fills — takers walk visible depth, resting orders
fill only once the opposite side reaches their price, no shorts, no overdraft."""
import oraclebook as ob
from oraclebook.core.book import PositionTracker
from oraclebook.core.clock import ReplayClock
from oraclebook.core.events import BookUpdate
from oraclebook.core.paper_executor import PaperExecutor


def _order(side=ob.Side.BUY, price=0.5, size=10.0, tif=ob.TimeInForce.GTC, token="UP", **kw):
    return ob.OrderRequest(token_id=token, market_id="m1", side=side, price=price, size=size,
                           order_type=ob.OrderType.LIMIT, strategy="t", time_in_force=tif, **kw)


def _book(bid, ask, ts=0.0, bids=(), asks=(), token="UP"):
    return BookUpdate(token_id=token, market_id="m1", best_bid=bid, best_ask=ask,
                      bids=bids, asks=asks, ts=ts)


def _ex(balance=100.0, **kw):
    book = PositionTracker()
    return PaperExecutor(book, starting_balance=balance, clock=ReplayClock(), **kw), book


async def test_passive_bid_does_not_fill_on_touch_only_when_ask_reaches_it():
    ex, _ = _ex()
    ex.on_book(_book(0.48, 0.52))
    assert await ex.submit([_order(price=0.48)]) == []
    assert ex.on_book(_book(0.48, 0.50)) == []          # bid queue still there: no fill
    fills = ex.on_book(_book(0.45, 0.47))               # ask traded through 0.48
    assert len(fills) == 1 and fills[0].price == 0.48 and fills[0].size == 10
    assert fills[0].fill_source == "paper_maker"


async def test_resting_bid_at_level_fills_only_resting_size():
    ex, _ = _ex()
    ex.on_book(_book(0.48, 0.52))
    await ex.submit([_order(price=0.48)])
    fills = ex.on_book(_book(0.47, 0.48, asks=((0.48, 4.0), (0.49, 50.0))))
    assert [f.size for f in fills] == [4.0]
    assert ex.open_orders()[0].size == 6.0


async def test_marketable_buy_walks_depth_and_consumes_liquidity():
    ex, _ = _ex()
    ex.on_book(_book(0.49, 0.50, asks=((0.50, 5.0), (0.51, 3.0), (0.60, 100.0))))
    fills = await ex.submit([_order(price=0.51, size=10, tif=ob.TimeInForce.FAK)])
    assert [(f.price, f.size) for f in fills] == [(0.50, 5.0), (0.51, 3.0)]
    assert all(f.fill_source == "paper_taker" for f in fills)
    assert ex.open_orders() == []                       # FAK remainder cancelled
    again = await ex.submit([_order(price=0.51, size=1, tif=ob.TimeInForce.FAK)])
    assert again == []                                  # same book: liquidity already taken


async def test_fok_is_all_or_nothing():
    ex, _ = _ex()
    ex.on_book(_book(0.49, 0.50, asks=((0.50, 5.0),)))
    assert await ex.submit([_order(price=0.50, size=10, tif=ob.TimeInForce.FOK)]) == []
    assert ex.rejections[-1][1] == "FOK not fully fillable"
    assert await ex.get_balance() == 100.0


async def test_sell_without_inventory_is_rejected_and_mints_no_cash():
    ex, _ = _ex(balance=100.0)
    ex.on_book(_book(0.98, 0.99))
    assert await ex.submit([_order(side=ob.Side.SELL, price=0.98, size=1000)]) == []
    assert await ex.get_balance() == 100.0
    assert "no shorts" in ex.rejections[-1][1]


async def test_buy_beyond_cash_and_invalid_orders_are_rejected():
    ex, _ = _ex(balance=100.0)
    ex.on_book(_book(0.49, 0.50))
    assert await ex.submit([_order(price=0.5, size=1000)]) == []
    assert await ex.submit([_order(price=0.5, size=-100)]) == []
    assert await ex.submit([_order(price=float("nan"))]) == []
    assert await ex.get_balance() == 100.0
    assert len(ex.rejections) == 3


async def test_resting_buy_reserves_cash_and_cancel_releases_it():
    ex, _ = _ex(balance=100.0)
    ex.on_book(_book(0.40, 0.60))
    o = _order(price=0.40, size=100)
    await ex.submit([o])
    assert abs(await ex.get_balance() - 60.0) < 1e-9
    assert await ex.cancel(o.id) is True
    assert await ex.get_balance() == 100.0


async def test_fees_charged_by_maker_taker_model():
    def fee(price, size, is_maker):
        return 0.0 if is_maker else 0.07 * price * (1 - price) * size

    ex, _ = _ex(balance=100.0, fee_model=fee)
    ex.on_book(_book(0.49, 0.50))
    taker = await ex.submit([_order(price=0.50, size=10)])
    assert abs(taker[0].fee - 0.175) < 1e-12
    await ex.submit([_order(price=0.45, size=10)])
    maker = ex.on_book(_book(0.40, 0.44))
    assert maker[0].fee == 0.0


async def test_latency_delays_activation_to_a_later_book():
    ex, _ = _ex(latency_s=0.5)
    ex.on_book(_book(0.49, 0.50, ts=0.0))
    assert await ex.submit([_order(price=0.50)]) == []  # in flight
    assert ex.on_book(_book(0.49, 0.50, ts=0.2)) == []  # not yet at the venue
    fills = ex.on_book(_book(0.49, 0.50, ts=0.6))
    assert len(fills) == 1 and fills[0].fill_source == "paper_taker"


async def test_gtd_order_expires():
    ex, _ = _ex()
    ex.on_book(_book(0.48, 0.52, ts=0.0))
    await ex.submit([_order(price=0.48, tif=ob.TimeInForce.GTD, expiration=10)])
    assert ex.on_book(_book(0.40, 0.45, ts=10.0)) == []
    assert ex.open_orders() == []
    assert await ex.get_balance() == 100.0
