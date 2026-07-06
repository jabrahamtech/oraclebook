"""Bus: pub/sub, coalescing vs raw, typed event fan-out, and command routing."""
from oraclebook import Bus
from oraclebook.core.events import BookUpdate, OrderFilled, SubmitOrder


async def test_pubsub_delivers():
    bus = Bus()
    sub = bus.subscribe("t")
    await bus.publish("t", 42)
    assert sub.get_nowait() == 42


async def test_coalescing_keeps_latest_per_key():
    bus = Bus(coalescing={"book_update": "token_id"})
    sub = bus.subscribe("book_update")
    await bus.publish("book_update", BookUpdate(token_id="A", best_bid=0.1))
    await bus.publish("book_update", BookUpdate(token_id="A", best_bid=0.2))
    assert sub.get_nowait().best_bid == 0.2  # latest wins
    assert sub.empty()                        # the earlier one was coalesced away


async def test_subscribe_raw_keeps_every_event():
    bus = Bus(coalescing={"book_update": "token_id"})
    sub = bus.subscribe_raw("book_update")
    await bus.publish("book_update", BookUpdate(token_id="A", best_bid=0.1))
    await bus.publish("book_update", BookUpdate(token_id="A", best_bid=0.2))
    assert sub.qsize() == 2


async def test_typed_event_fanout():
    bus = Bus()
    q = bus.subscribe_event(OrderFilled)
    await bus.publish_event(OrderFilled())
    assert not q.empty()


async def test_command_routing():
    bus = Bus()
    seen = []

    async def handler(cmd):
        seen.append(cmd)
        return "ok"

    bus.register_handler(SubmitOrder, handler)
    assert await bus.send(SubmitOrder()) == "ok"
    assert len(seen) == 1
