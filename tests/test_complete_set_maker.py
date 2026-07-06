"""CompleteSetMaker: quotes both legs only when the complete-set edge clears the floor."""
import oraclebook as ob
from oraclebook.core.events import BookUpdate


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
