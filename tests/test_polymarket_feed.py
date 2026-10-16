"""PolymarketFeed: local L2 book maintenance, message handling, reconnect hygiene and
market rediscovery — offline, against a fake aiohttp module (no network)."""
import asyncio
import json
import sys
import time
import types

from oraclebook import Market
from oraclebook.adapters.polymarket import feed as feed_mod
from oraclebook.adapters.polymarket.feed import PolymarketFeed
from oraclebook.core.bus import Bus
from oraclebook.core.events import EVT_BOOK_INVALIDATED, EVT_BOOK_UPDATE

BOOK = {"event_type": "book", "asset_id": "YES", "market": "0xc", "timestamp": "1000",
        "bids": [{"price": "0.48", "size": "100"}, {"price": "0.47", "size": "50"}],
        "asks": [{"price": "0.52", "size": "80"}]}


def _feed(**kw):
    bus = Bus()
    sub = bus.subscribe_raw(EVT_BOOK_UPDATE)
    f = PolymarketFeed(bus, ["BTC"], [5], **kw)
    f._token_market = {"YES": "m1", "NO": "m1"}
    return f, bus, sub


def _drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def _price_change(*changes, ts="2000"):
    return json.dumps({"event_type": "price_change", "market": "0xc", "timestamp": ts,
                       "price_changes": list(changes)})


async def test_book_snapshot_publishes_sorted_depth():
    f, _, sub = _feed()
    await f._on_message(json.dumps(BOOK))
    (u,) = _drain(sub)
    assert (u.best_bid, u.best_ask) == (0.48, 0.52)
    assert u.bids == ((0.48, 100.0), (0.47, 50.0))
    assert u.asks == ((0.52, 80.0),)
    assert u.market_id == "m1"


async def test_price_change_keeps_full_depth():
    f, _, sub = _feed()
    await f._on_message(json.dumps(BOOK))
    await f._on_message(_price_change(
        {"asset_id": "YES", "price": "0.49", "size": "10", "side": "BUY", "hash": "h",
         "best_bid": "0.49", "best_ask": "0.52"}))
    latest = _drain(sub)[-1]
    assert latest.best_bid == 0.49
    assert latest.bids == ((0.49, 10.0), (0.48, 100.0), (0.47, 50.0))
    assert latest.asks == ((0.52, 80.0),)


async def test_price_change_size_zero_removes_level():
    f, _, sub = _feed()
    await f._on_message(json.dumps(BOOK))
    await f._on_message(_price_change(
        {"asset_id": "YES", "price": "0.48", "size": "0", "side": "BUY", "hash": "h"}))
    ups = _drain(sub)
    assert len(ups) == 2 and ups[-1].best_bid == 0.47
    assert ups[-1].bids == ((0.47, 50.0),)


async def test_price_change_sets_level_size_and_updates_asks():
    f, _, sub = _feed()
    await f._on_message(json.dumps(BOOK))
    await f._on_message(_price_change(
        {"asset_id": "YES", "price": "0.52", "size": "30", "side": "SELL"},
        {"asset_id": "YES", "price": "0.51", "size": "5", "side": "SELL"}))
    (u,) = _drain(sub)[1:]  # one update per touched token per message
    assert u.asks == ((0.51, 5.0), (0.52, 30.0))


async def test_price_change_before_snapshot_is_ignored():
    f, _, sub = _feed()
    await f._on_message(_price_change(
        {"asset_id": "NO", "price": "0.5", "size": "1", "side": "BUY"}))
    assert _drain(sub) == []


async def test_malformed_levels_are_skipped_not_raised():
    f, _, sub = _feed()
    bad = dict(BOOK, bids=[{"price": "0.48"}, {"size": "3"}, {"price": "x", "size": "1"},
                           {"price": "0.46", "size": "7"}])
    await f._on_message(json.dumps(bad))
    await f._on_message(_price_change({"asset_id": "YES", "side": "BUY", "price": "0.4"}))
    (u,) = _drain(sub)
    assert u.bids == ((0.46, 7.0),)


async def test_fine_tick_prices_are_kept():
    f, _, sub = _feed()
    await f._on_message(json.dumps(dict(BOOK, bids=[{"price": "0.0003", "size": "9"}],
                                        asks=[{"price": "0.9998", "size": "4"}])))
    (u,) = _drain(sub)
    assert (u.best_bid, u.best_ask) == (0.0003, 0.9998)


async def test_tick_size_change_is_stored_and_trades_are_ignored():
    f, _, sub = _feed()
    await f._on_message(json.dumps({"event_type": "tick_size_change", "asset_id": "YES",
                                    "market": "0xc", "old_tick_size": "0.01",
                                    "new_tick_size": "0.001", "timestamp": "5"}))
    await f._on_message(json.dumps({"event_type": "last_trade_price", "asset_id": "YES",
                                    "market": "0xc", "price": "0.5", "size": "10",
                                    "side": "BUY", "fee_rate_bps": "0", "timestamp": "6"}))
    assert f.tick_size("YES") == "0.001"
    assert _drain(sub) == []


async def test_exchange_timestamp_drives_event_ts_and_age():
    f, _, sub = _feed()
    sent_ms = time.time() * 1000.0 - 250.0
    await f._on_message(json.dumps(dict(BOOK, timestamp=str(int(sent_ms)))))
    (u,) = _drain(sub)
    assert abs(u.ts - int(sent_ms) / 1000.0) < 1e-6
    assert 200.0 <= u.age_ms < 5000.0


async def test_missing_timestamp_uses_receive_time():
    f, _, sub = _feed()
    before = time.time()
    await f._on_message(json.dumps({k: v for k, v in BOOK.items() if k != "timestamp"}))
    (u,) = _drain(sub)
    assert u.ts >= before and u.age_ms == 0.0


def _fake_aiohttp(monkeypatch, sessions):
    class WSMsgType:
        TEXT, CLOSED, ERROR = 1, 2, 3

    class FakeWS:
        def __init__(self):
            self.closed = False
            self.sent = []

        async def send_json(self, obj):
            self.sent.append(obj)

        async def send_str(self, s):
            self.sent.append(s)

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(0.03)  # server closes the socket after 30ms
            self.closed = True
            raise StopAsyncIteration

        async def close(self):
            self.closed = True

    class Session:
        def __init__(self, *a, **k):
            self.closed = False
            self.sockets = []
            sessions.append(self)

        async def ws_connect(self, url):
            self.sockets.append(FakeWS())
            return self.sockets[-1]

        async def close(self):
            self.closed = True

    aiohttp = types.ModuleType("aiohttp")
    aiohttp.ClientSession = Session
    aiohttp.ClientTimeout = lambda **k: None
    aiohttp.WSMsgType = WSMsgType
    monkeypatch.setitem(sys.modules, "aiohttp", aiohttp)


def _pingers():
    return [t for t in asyncio.all_tasks()
            if t.get_coro().__qualname__ == "PolymarketFeed._ping" and not t.done()]


async def test_reconnect_backs_off_and_does_not_leak_ping_tasks(monkeypatch):
    sessions = []
    _fake_aiohttp(monkeypatch, sessions)
    f, bus, _ = _feed(ping_interval=0.005, backoff_initial=0.05, backoff_max=0.2)
    invalidated = bus.subscribe_raw(EVT_BOOK_INVALIDATED)
    f._running = True
    f._task = asyncio.create_task(f._run())
    await asyncio.sleep(0.2)
    connects = len(sessions[0].sockets)
    assert len(_pingers()) <= 1
    # 30ms sessions with 50ms -> 100ms -> 200ms backoff: at most 3 connects in 200ms.
    assert 2 <= connects <= 3
    inv = _drain(invalidated)
    assert inv and set(inv[0].token_ids) == {"YES", "NO"}
    await f.stop()
    await asyncio.sleep(0)
    assert _pingers() == []
    assert sessions[0].closed


async def test_rediscovery_subscribes_new_tokens_and_drops_ended(monkeypatch):
    now = time.time()
    rounds = [
        [Market(venue="polymarket", market_id="m1", condition_id="0x1", token_ids=("a", "b"),
                end_ts=now + 0.05)],
        [Market(venue="polymarket", market_id="m2", condition_id="0x2", token_ids=("c", "d"),
                end_ts=now + 300)],
    ]
    monkeypatch.setattr(feed_mod, "discover", lambda a, i: rounds.pop(0) if rounds else [])
    f = PolymarketFeed(Bus(), ["BTC"], [5], rediscover_interval=0.06)

    class _WS:
        def __init__(self):
            self.closed = False
            self.sent = []

        async def send_json(self, obj):
            self.sent.append(obj)

        async def close(self):
            self.closed = True

    async def _idle():
        await asyncio.sleep(3600)

    monkeypatch.setattr(f, "_run", _idle)
    await f.start()
    assert set(f._token_market) == {"a", "b"}
    f._ws = _WS()
    await asyncio.sleep(0.1)
    await f.stop()
    assert set(f._token_market) == {"c", "d"}
    assert {"operation": "subscribe", "assets_ids": ["c", "d"], "level": 2} in f._ws.sent
    assert {"operation": "unsubscribe", "assets_ids": ["a", "b"]} in f._ws.sent


async def test_rediscovery_keeps_live_tokens_missing_from_a_round(monkeypatch):
    f = PolymarketFeed(Bus(), ["BTC"], [5])
    live = Market(venue="polymarket", market_id="m1", condition_id="0x1", token_ids=("a", "b"),
                  end_ts=time.time() + 300)
    await f._apply_markets([live])
    await f._apply_markets([])  # a transient Gamma miss must not drop a running market
    assert set(f._token_market) == {"a", "b"}
