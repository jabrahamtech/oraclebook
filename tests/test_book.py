"""Book/PositionTracker: weighted-average entry, sell-to-flat, deployed notional."""
from oraclebook import Book, Fill, PositionTracker, Side


def _fill(token, side, price, size, market="m1"):
    return Fill(order_id="o", token_id=token, market_id=market, side=side, price=price, size=size)


def test_position_tracker_is_a_book():
    assert isinstance(PositionTracker(), Book)


def test_weighted_average_entry():
    b = PositionTracker()
    b.on_fill(_fill("UP", Side.BUY, 0.50, 10))
    b.on_fill(_fill("UP", Side.BUY, 0.60, 10))
    p = b.get("UP")
    assert p.size == 20
    assert abs(p.avg_entry - 0.55) < 1e-9


def test_sell_reduces_and_drops_when_flat():
    b = PositionTracker()
    b.on_fill(_fill("UP", Side.BUY, 0.50, 10))
    b.on_fill(_fill("UP", Side.SELL, 0.70, 10))
    assert b.get("UP") is None
    assert b.positions() == []


def test_total_deployed():
    b = PositionTracker()
    b.on_fill(_fill("UP", Side.BUY, 0.50, 10))
    assert abs(b.total_deployed() - 5.0) < 1e-9
