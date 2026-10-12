"""Risk: each Gate, the run_gates chain (short-circuit), and the CircuitBreaker."""
from oraclebook import (
    BalanceGate,
    CircuitBreaker,
    DailyLossGate,
    FeedStalenessGate,
    Fill,
    InventoryGate,
    MaxPositionsGate,
    OrderRequest,
    OrderSizeGate,
    OrderType,
    OrderValidityGate,
    PerMarketCapGate,
    PositionTracker,
    RiskContext,
    Side,
    TotalExposureGate,
    run_gates,
)

NAN = float("nan")


def _buy(price=0.5, size=10, book_age_ms=0.0, market="m1", token="t1"):
    return OrderRequest(token_id=token, market_id=market, side=Side.BUY, price=price, size=size,
                        order_type=OrderType.LIMIT, strategy="s", book_age_ms=book_age_ms)


def _sell(price=0.5, size=10, market="m1", token="t1"):
    return OrderRequest(token_id=token, market_id=market, side=Side.SELL, price=price, size=size,
                        order_type=OrderType.LIMIT, strategy="s")


def _ctx(balance=1000.0, daily_pnl=0.0, book=None, open_orders=()):
    return RiskContext(balance=balance, daily_pnl=daily_pnl, book=book or PositionTracker(),
                       open_orders=tuple(open_orders))


def _holding(token="t1", size=10.0, price=0.5, market="m1"):
    b = PositionTracker()
    b.on_fill(Fill(order_id="o", token_id=token, market_id=market, side=Side.BUY, price=price,
                   size=size))
    return b


def _full_chain(cap=50.0):
    return [OrderValidityGate(), InventoryGate(), FeedStalenessGate(2000), DailyLossGate(20),
            OrderSizeGate(1.0, 25.0), BalanceGate(), PerMarketCapGate(cap),
            TotalExposureGate(cap), MaxPositionsGate(1)]


def test_feed_staleness():
    g = FeedStalenessGate(2000)
    assert g.check([_buy(book_age_ms=500)], _ctx()).passed
    assert not g.check([_buy(book_age_ms=5000)], _ctx()).passed


def test_daily_loss():
    assert not DailyLossGate(5).check([_buy()], _ctx(daily_pnl=-6)).passed
    assert DailyLossGate(5).check([_buy()], _ctx(daily_pnl=-1)).passed


def test_balance():
    assert not BalanceGate().check([_buy(price=0.9, size=200)], _ctx(balance=50)).passed
    assert BalanceGate().check([_buy(price=0.1, size=10)], _ctx(balance=50)).passed


def test_order_size():
    assert not OrderSizeGate(min_notional_usd=10).check([_buy(price=0.1, size=1)], _ctx()).passed


def test_per_market_cap_and_max_positions():
    b = PositionTracker()
    b.on_fill(Fill(order_id="o", token_id="UP", market_id="m1", side=Side.BUY, price=0.5, size=100))
    ctx = _ctx(book=b)
    order = _buy(price=0.5, size=20, token="DN")  # +$10 -> $60 on m1
    assert not PerMarketCapGate(55).check([order], ctx).passed
    assert PerMarketCapGate(100).check([order], ctx).passed
    assert not MaxPositionsGate(1).check([order], ctx).passed


def test_total_exposure():
    b = PositionTracker()
    b.on_fill(Fill(order_id="o", token_id="UP", market_id="m1", side=Side.BUY, price=0.5, size=100))
    assert not TotalExposureGate(55).check([_buy(price=0.5, size=20, token="DN")], _ctx(book=b)).passed


def test_run_gates_short_circuits_on_first_failure():
    chain = [FeedStalenessGate(2000), BalanceGate()]
    res = run_gates(chain, [_buy(book_age_ms=9999)], _ctx())
    assert not res.passed and res.gate == "feed_stale"
    assert run_gates(chain, [_buy()], _ctx()).passed


def test_circuit_breaker_trips_and_fires_on_trip_once():
    fired = []
    cb = CircuitBreaker(max_consecutive_losses=2, on_trip=fired.append)
    cb.record_loss()
    assert not cb.is_tripped
    cb.record_loss()
    assert cb.is_tripped and len(fired) == 1
    cb.record_loss()
    assert len(fired) == 1  # not re-fired while already tripped


def test_order_validity_rejects_malformed_orders_of_either_side():
    g = OrderValidityGate()
    for bad in (_buy(size=NAN), _buy(price=NAN), _buy(price=float("inf")), _buy(size=0),
                _buy(size=-1), _buy(price=0.0), _buy(price=1.0), _buy(price=1.5),
                _sell(size=NAN), _sell(price=1.2)):
        res = g.check([bad], _ctx())
        assert not res.passed and res.gate == "order_validity", (bad.price, bad.size)
    assert g.check([_buy(price=0.37, size=5), _sell(price=0.99, size=1)], _ctx()).passed


def test_order_validity_tick_size():
    g = OrderValidityGate(tick_size=0.01)
    assert g.check([_buy(price=0.37)], _ctx()).passed
    assert g.check([_buy(price=0.1 + 0.2)], _ctx()).passed       # float noise is on-tick
    assert not g.check([_buy(price=0.375)], _ctx()).passed


def test_inventory_rejects_naked_and_oversized_sells():
    g = InventoryGate()
    naked = g.check([_sell(price=0.01, size=1_000_000)], _ctx(balance=0.0))
    assert not naked.passed and "cannot short" in naked.reason
    assert not g.check([_sell(size=1000)], _ctx(book=_holding(size=10))).passed
    assert g.check([_sell(size=10)], _ctx(book=_holding(size=10))).passed


def test_inventory_counts_resting_and_same_batch_sells():
    g = InventoryGate()
    held = _holding(size=10)
    assert not g.check([_sell(size=4)], _ctx(book=held, open_orders=[_sell(size=7)])).passed
    assert g.check([_sell(size=3)], _ctx(book=held, open_orders=[_sell(size=7)])).passed
    assert not g.check([_sell(size=6), _sell(size=6)], _ctx(book=held)).passed
    assert g.check([_sell(size=5), _buy(), _sell(size=5)], _ctx(book=held)).passed


def test_full_chain_rejects_naked_sell_and_oversell():
    assert not run_gates(_full_chain(), [_sell(price=0.01, size=1_000_000)],
                         _ctx(balance=0.0)).passed
    assert not run_gates(_full_chain(), [_sell(size=1000)], _ctx(book=_holding())).passed


def test_full_chain_rejects_nan_and_out_of_range_price():
    assert not run_gates(_full_chain(), [_buy(size=NAN)], _ctx(balance=0.0)).passed
    assert not run_gates(_full_chain(), [_buy(price=1.5)], _ctx(balance=100.0)).passed


def test_gates_fail_closed_on_nan_without_validity_gate():
    nan_buy = [_buy(size=NAN)]
    for g in (OrderSizeGate(1.0, 25.0), BalanceGate(), PerMarketCapGate(50),
              TotalExposureGate(50)):
        assert not g.check(nan_buy, _ctx(balance=0.0)).passed, g.name
    assert not FeedStalenessGate(2000).check([_buy(book_age_ms=NAN)], _ctx()).passed
    assert not DailyLossGate(20).check([_buy()], _ctx(daily_pnl=NAN)).passed
    assert not BalanceGate().check([_buy()], _ctx(balance=NAN)).passed
    assert not InventoryGate().check([_sell(size=NAN)], _ctx(book=_holding())).passed


def test_exposure_gates_count_resting_buys():
    resting = [_buy(price=0.5, size=20, token=f"r{i}", market="m1") for i in range(4)]  # $40
    ctx = _ctx(open_orders=resting)
    order = _buy(price=0.5, size=30, token="new", market="m1")                          # +$15
    assert not TotalExposureGate(50).check([order], ctx).passed
    assert TotalExposureGate(60).check([order], ctx).passed
    assert not PerMarketCapGate(50).check([order], ctx).passed
    assert PerMarketCapGate(50).check([_buy(price=0.5, size=30, market="m2")], ctx).passed
    assert not MaxPositionsGate(4).check([order], ctx).passed
    assert MaxPositionsGate(4).check([_buy(token="r0")], ctx).passed  # adds to a resting token


def test_resting_sells_do_not_count_as_exposure():
    ctx = _ctx(book=_holding(size=100), open_orders=[_sell(size=100)])
    assert TotalExposureGate(70).check([_buy(price=0.5, size=20, token="t2")], ctx).passed


def test_balance_gate_checks_new_batch_only():
    ctx = _ctx(balance=10.0, open_orders=[_buy(price=0.5, size=100)])
    assert BalanceGate().check([_buy(price=0.5, size=20)], ctx).passed


def test_exposure_caps_hold_across_batches_of_resting_orders():
    gates = [TotalExposureGate(50.0), PerMarketCapGate(20.0), MaxPositionsGate(2)]
    resting: list[OrderRequest] = []
    for i in range(20):  # 20 markets x $10 each; nothing ever fills
        batch = [_buy(price=0.5, size=20, token=f"t{i}", market=f"m{i % 2}")]
        if run_gates(gates, batch, _ctx(open_orders=resting)).passed:
            resting += batch
    assert sum(o.price * o.size for o in resting) <= 20.0
    assert len({o.token_id for o in resting}) <= 2
