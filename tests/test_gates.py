"""Risk: each Gate, the run_gates chain (short-circuit), and the CircuitBreaker."""
from oraclebook import (
    BalanceGate,
    CircuitBreaker,
    DailyLossGate,
    FeedStalenessGate,
    Fill,
    MaxPositionsGate,
    OrderRequest,
    OrderSizeGate,
    OrderType,
    PerMarketCapGate,
    PositionTracker,
    RiskContext,
    Side,
    TotalExposureGate,
    run_gates,
)


def _buy(price=0.5, size=10, book_age_ms=0.0, market="m1", token="t1"):
    return OrderRequest(token_id=token, market_id=market, side=Side.BUY, price=price, size=size,
                        order_type=OrderType.LIMIT, strategy="s", book_age_ms=book_age_ms)


def _ctx(balance=1000.0, daily_pnl=0.0, book=None):
    return RiskContext(balance=balance, daily_pnl=daily_pnl, book=book or PositionTracker())


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
