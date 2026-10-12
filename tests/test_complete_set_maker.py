"""CompleteSetMaker: quotes every leg only when a full, fresh, non-crossing set clears the floor."""
import oraclebook as ob
from oraclebook.core.events import BookUpdate, OrderFilled


def _book(token, market, bid):
    return BookUpdate(token_id=token, market_id=market, best_bid=bid)


async def test_quotes_both_legs_when_edge_clears():
    s = ob.CompleteSetMaker(min_edge=0.02, size=10.0)
    assert await s.on_book(_book("UP", "m1", 0.48)) == []          # only one leg so far
    orders = await s.on_book(_book("DN", "m1", 0.48))              # edge = 1 - 0.96 = 0.04
    assert len(orders) == 2
    assert {o.token_id for o in orders} == {"UP", "DN"}
    assert all(o.side is ob.Side.BUY for o in orders)


async def test_no_quote_when_edge_below_floor():
    s = ob.CompleteSetMaker(min_edge=0.02)
    await s.on_book(_book("UP", "m1", 0.50))
    orders = await s.on_book(_book("DN", "m1", 0.49))              # edge = 0.01 < 0.02
    assert orders == []


async def test_quotes_a_market_only_once():
    s = ob.CompleteSetMaker(min_edge=0.02)
    await s.on_book(_book("UP", "m1", 0.48))
    assert len(await s.on_book(_book("DN", "m1", 0.48))) == 2      # first time -> quotes
    assert await s.on_book(_book("UP", "m1", 0.47)) == []          # already quoted m1


def _bu(token, bid, ask=0.0, ts=1000.0, market="m1", age_ms=0.0):
    return BookUpdate(token_id=token, market_id=market, best_bid=bid, best_ask=ask, ts=ts,
                      age_ms=age_ms)


async def _quote(s, *updates):
    out = []
    for u in updates:
        out = await s.on_book(u)
    return out


async def test_equal_edges_at_the_floor_treated_equally():
    for a, b in ((0.48, 0.50), (0.05, 0.93)):  # both exactly a 2-cent edge
        orders = await _quote(ob.CompleteSetMaker(min_edge=0.02), _bu("UP", a), _bu("DN", b))
        assert len(orders) == 2, (a, b)


async def test_wiped_bid_drops_the_cached_leg():
    s = ob.CompleteSetMaker(min_edge=0.02)
    orders = await _quote(s, _bu("UP", 0.48, 0.50), _bu("UP", 0.0, 0.30), _bu("DN", 0.48, 0.70))
    assert orders == []


async def test_does_not_quote_a_crossing_leg():
    s = ob.CompleteSetMaker(min_edge=0.02)
    assert await _quote(s, _bu("UP", 0.48, 0.48), _bu("DN", 0.48, 0.50)) == []
    assert len(await _quote(s, _bu("UP", 0.48, 0.49))) == 2


async def test_stale_leg_blocks_quoting_and_sets_book_age():
    s = ob.CompleteSetMaker(min_edge=0.02, max_leg_age_s=2.0)
    assert await _quote(s, _bu("UP", 0.48, ts=1000.0), _bu("DN", 0.48, ts=1003.0)) == []
    orders = await s.on_book(_bu("UP", 0.48, ts=1003.5, age_ms=200))
    assert len(orders) == 2
    assert all(abs(o.book_age_ms - 500.0) < 1e-6 for o in orders)  # DN cached 500ms ago


async def test_staleness_gate_sees_the_stale_leg():
    s = ob.CompleteSetMaker(min_edge=0.02, max_leg_age_s=3600.0)
    orders = await _quote(s, _bu("UP", 0.48, age_ms=600_000), _bu("DN", 0.48, age_ms=5))
    assert orders
    ctx = ob.RiskContext(balance=100.0, daily_pnl=0.0, book=ob.PositionTracker())
    assert not ob.run_gates([ob.FeedStalenessGate(2000)], orders, ctx).passed


async def test_three_outcome_market_needs_every_leg():
    s = ob.CompleteSetMaker(min_edge=0.02, legs_per_market=3)
    assert await _quote(s, _bu("A", 0.30), _bu("B", 0.30)) == []
    assert await s.on_book(_bu("C", 0.39)) == []                 # full set cost 0.99 < floor
    assert len(await s.on_book(_bu("C", 0.37))) == 3


async def test_markets_mapping_requires_exactly_the_listed_tokens():
    s = ob.CompleteSetMaker(min_edge=0.02, markets={"m1": ("A", "B", "C")})
    assert await _quote(s, _bu("A", 0.30), _bu("B", 0.30), _bu("X", 0.01)) == []
    assert await _quote(s, _bu("Z", 0.30, market="other"), _bu("Y", 0.30, market="other")) == []
    orders = await s.on_book(_bu("C", 0.30))
    assert {o.token_id for o in orders} == {"A", "B", "C"}


async def test_ttl_quotes_are_gtd_and_requote_only_after_expiry():
    s = ob.CompleteSetMaker(min_edge=0.02, quote_ttl_s=30.0)
    orders = await _quote(s, _bu("UP", 0.48, ts=1000.0), _bu("DN", 0.48, ts=1000.5))
    assert all(o.time_in_force is ob.TimeInForce.GTD and o.expiration == 1031 for o in orders)
    assert await s.on_book(_bu("UP", 0.47, ts=1020.0)) == []
    assert await s.on_book(_bu("DN", 0.47, ts=1030.9)) == []
    assert len(await s.on_book(_bu("UP", 0.47, ts=1031.0))) == 2
    assert await s.on_book(_bu("DN", 0.47, ts=1031.0)) == []


async def test_default_quotes_are_gtc_once_only():
    s = ob.CompleteSetMaker(min_edge=0.02)
    orders = await _quote(s, _bu("UP", 0.48), _bu("DN", 0.48))
    assert all(o.time_in_force is ob.TimeInForce.GTC and o.expiration == 0 for o in orders)
    assert await _quote(s, _bu("UP", 0.40, ts=9999.0), _bu("DN", 0.40, ts=9999.0)) == []


async def test_unhedged_tracks_one_leg_fills():
    s = ob.CompleteSetMaker(min_edge=0.02, size=10)
    orders = await _quote(s, _bu("UP", 0.48), _bu("DN", 0.48))
    up, dn = sorted(orders, key=lambda o: o.token_id != "UP")

    def fill(o, size, strategy=s.name):
        f = ob.Fill(order_id=o.id, token_id=o.token_id, market_id="m1", side=ob.Side.BUY,
                    price=o.price, size=size, strategy=strategy)
        return OrderFilled(fills=(f,), orders=(o,))

    assert s.unhedged("m1") == 0.0
    await s.on_fill(fill(up, 10))
    assert s.unhedged("m1") == 10.0
    await s.on_fill(fill(dn, 4, strategy="someone_else"))
    assert s.unhedged("m1") == 10.0
    await s.on_fill(fill(dn, 10))
    assert s.unhedged("m1") == 0.0
