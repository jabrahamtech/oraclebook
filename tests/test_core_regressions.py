"""Regressions for audited core defects: consumer death, gate races, realised PnL and the
daily-loss stop, settlement, replay lookahead, coalescing starvation, ledger/recording reuse."""
import asyncio
import json

import oraclebook as ob
from oraclebook.core.bus import Bus, CoalescingBuffer
from oraclebook.core.events import EVT_BOOK_UPDATE, EVT_PRICE_TICK, BookUpdate, PriceTick
from oraclebook.core.ledger import Ledger
from oraclebook.replay import Recorder, run_replay
from oraclebook.replay.replay_feed import load_recording


def _buy(token, price, size=10.0, market="m1"):
    return ob.OrderRequest(token_id=token, market_id=market, side=ob.Side.BUY, price=price,
                           size=size, order_type=ob.OrderType.LIMIT, strategy="t")


class TakeAsk(ob.BaseStrategy):
    name = "take_ask"

    async def on_book(self, book):
        return [_buy(book.token_id, book.best_ask, 2.0)] if book.best_ask > 0 else []


class FlakyExecutor:
    def __init__(self):
        self.calls = 0

    async def submit(self, orders):
        self.calls += 1
        if self.calls == 1:
            raise ConnectionError("transient venue 502")
        return []

    async def cancel(self, venue_order_id):
        return True

    async def cancel_all(self):
        return 0

    async def get_balance(self):
        return 1000.0

    def get_position(self, token_id):
        return None


async def test_executor_exception_does_not_kill_the_consumer():
    ex = FlakyExecutor()
    app = ob.App(strategy=TakeAsk(), executor=ex)
    await app.start()
    for i in range(3):
        await app.bus.publish(EVT_BOOK_UPDATE, BookUpdate(token_id=f"T{i}", market_id="m",
                                                          best_bid=0.4, best_ask=0.5))
        await asyncio.sleep(0.02)
    await app.stop()
    assert ex.calls == 3


async def test_concurrent_batches_cannot_race_past_the_balance_gate():
    class SlowPaper(ob.PaperExecutor):
        async def submit(self, orders):
            await asyncio.sleep(0.01)
            return await super().submit(orders)

    class PriceBuyer(ob.BaseStrategy):
        name = "pb"

        async def on_price(self, tick):
            return [_buy("UP", 0.5, 10.0)]

    book = ob.PositionTracker()
    ex = SlowPaper(book, starting_balance=6.0)
    app = ob.App(strategy=PriceBuyer(), executor=ex, book=book, gates=[ob.BalanceGate()])
    await app.start()
    ex.on_book(BookUpdate(token_id="UP", market_id="m1", best_bid=0.49, best_ask=0.5))
    await app.bus.publish(EVT_PRICE_TICK, PriceTick(symbol="A", price=1.0))
    await app.bus.publish(EVT_PRICE_TICK, PriceTick(symbol="B", price=1.0))
    await asyncio.sleep(0.1)
    await app.stop()
    assert book.get("UP").size == 10.0
    assert await ex.get_balance() >= 0.0


async def test_trading_losses_feed_daily_pnl_and_trip_the_daily_loss_gate():
    book = ob.PositionTracker()
    book.on_fill(ob.Fill(order_id="1", token_id="UP", market_id="m", side=ob.Side.BUY,
                         price=0.80, size=100, fee=0.5))
    book.on_fill(ob.Fill(order_id="2", token_id="UP", market_id="m", side=ob.Side.SELL,
                         price=0.20, size=100, fee=0.5))
    assert abs(book.realized_pnl - (-61.0)) < 1e-9
    app = ob.App(strategy=ob.BaseStrategy(), book=book, clock=ob.ReplayClock(start=86_400.0 * 3))
    assert abs(app.daily_pnl) < 1e-9          # day boundary snapshot: losses were "yesterday"
    book.realized_pnl -= 60.0
    ctx = ob.RiskContext(balance=100.0, daily_pnl=app.daily_pnl, book=book)
    assert not ob.DailyLossGate(50.0).check([_buy("X", 0.5)], ctx).passed


async def test_oversell_is_clamped_to_holdings():
    book = ob.PositionTracker()
    book.on_fill(ob.Fill(order_id="1", token_id="UP", market_id="m", side=ob.Side.BUY,
                         price=0.5, size=10))
    book.on_fill(ob.Fill(order_id="2", token_id="UP", market_id="m", side=ob.Side.SELL,
                         price=0.6, size=30))
    assert book.get("UP") is None
    assert abs(book.realized_pnl - 1.0) < 1e-9


async def test_resolve_settles_books_pnl_credits_cash_and_records(tmp_path):
    ledger = Ledger(str(tmp_path / "l.jsonl"))
    app = ob.App(strategy=ob.BaseStrategy(), starting_balance=100.0, ledger=ledger)
    app.executor.on_book(BookUpdate(token_id="UP", market_id="m1", best_bid=0.39, best_ask=0.4))
    await app._process([_buy("UP", 0.4, 10.0)])
    res = await app.resolve("UP", 1.0)
    assert abs(res.pnl - 6.0) < 1e-9
    assert abs(await app.executor.get_balance() - 106.0) < 1e-9
    assert app.book.get("UP") is None
    assert [r["payload"]["pnl"] for r in ledger.resolutions()] == [res.pnl]


async def test_replay_fill_ts_equals_triggering_event_even_when_strategy_awaits(tmp_path):
    class SlowTaker(TakeAsk):
        async def on_book(self, book):
            for _ in range(3):
                await asyncio.sleep(0)
            return await super().on_book(book)

    rec = tmp_path / "rec.jsonl"
    rec.write_text("".join(
        json.dumps({"seq": i, "topic": "book_update", "ts": 1000.0 + i * 60, "type": "BookUpdate",
                    "payload": {"token_id": f"T{i}", "market_id": "m", "ts": 1000.0 + i * 60,
                                "best_bid": 0.5, "best_ask": 0.51}}) + "\n"
        for i in range(10)
    ))
    app = await run_replay(str(rec), SlowTaker(), ledger_path=str(tmp_path / "l.jsonl"))
    fills = [f["payload"] for f in app.ledger.fills()]
    assert len(fills) == 10
    assert all(f["ts"] == 1000.0 + int(f["token_id"][1:]) * 60 for f in fills)


async def test_rerunning_replay_into_the_same_ledger_does_not_double_count(tmp_path):
    rec = tmp_path / "rec.jsonl"
    rec.write_text(json.dumps({"seq": 0, "topic": "book_update", "ts": 1.0, "type": "BookUpdate",
                               "payload": {"token_id": "UP", "market_id": "m", "ts": 1.0,
                                           "best_bid": 0.4, "best_ask": 0.41}}) + "\n")
    path = str(tmp_path / "l.jsonl")
    await run_replay(str(rec), TakeAsk(), ledger_path=path)
    app = await run_replay(str(rec), TakeAsk(), ledger_path=path)
    assert len(list(app.ledger.fills())) == 1


async def test_truncated_recording_replays_instead_of_hanging(tmp_path):
    rec = tmp_path / "rec.jsonl"
    good = json.dumps({"seq": 0, "topic": "book_update", "ts": 1.0, "type": "BookUpdate",
                       "payload": {"token_id": "UP", "market_id": "m", "ts": 1.0,
                                   "best_bid": 0.4, "best_ask": 0.41}})
    rec.write_text(good + "\n" + good[:25])
    app = await asyncio.wait_for(run_replay(str(rec), TakeAsk()), timeout=5)
    assert len(list(app.ledger.fills())) == 1


async def test_recorder_keeps_publish_order_and_continues_seq_across_sessions(tmp_path):
    path = str(tmp_path / "rec.jsonl")
    for start in (0.0, 100.0):
        bus = Bus()
        rec = Recorder(bus, path)
        await rec.start()
        await bus.publish(EVT_BOOK_UPDATE, BookUpdate(token_id="UP", ts=start + 1))
        await bus.publish(EVT_PRICE_TICK, PriceTick(symbol="BTC", ts=start + 2))
        await bus.publish(EVT_BOOK_UPDATE, BookUpdate(token_id="UP", ts=start + 3))
        await rec.stop()
    records = load_recording(path)
    assert [r["seq"] for r in records] == list(range(6))
    assert [r["ts"] for r in records] == [1, 2, 3, 101, 102, 103]


async def test_coalescing_buffer_does_not_starve_a_quiet_key():
    buf = CoalescingBuffer("token_id")
    buf.put_nowait(BookUpdate(token_id="NO", best_bid=0.4))
    delivered = set()
    for i in range(5):
        buf.put_nowait(BookUpdate(token_id="YES", best_bid=0.5 + i / 100))
        delivered.add(buf.get_nowait().token_id)
    assert "NO" in delivered
