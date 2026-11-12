"""Polymarket adapter: fee model, interval slugs, Venue conformance (offline), plus a
lenient live-Gamma discovery check (skipped if the API is unreachable)."""
import os
import urllib.error

import pytest

import oraclebook as ob
from oraclebook.adapters.polymarket import (
    PolymarketVenue,
    discover,
    fee_amount,
    taker_fee_per_share,
    updown_slug,
)
from oraclebook.adapters.polymarket.fees import CRYPTO_FEE_RATE
from oraclebook.adapters.polymarket.markets import interval_start


def test_fee_peaks_at_midpoint_and_maker_is_free():
    assert abs(taker_fee_per_share(0.5) - CRYPTO_FEE_RATE * 0.25) < 1e-9
    assert taker_fee_per_share(0.5) > taker_fee_per_share(0.10)
    assert taker_fee_per_share(0.5) > taker_fee_per_share(0.90)
    assert fee_amount(0.5, 100, is_maker=True) == 0.0
    assert abs(fee_amount(0.5, 100) - 100 * CRYPTO_FEE_RATE * 0.25) < 1e-6


def test_interval_alignment_and_slug():
    assert interval_start(5, 1779449400) == 1779449400  # already aligned
    assert interval_start(5, 1779449401) == 1779449400  # rounds down
    assert interval_start(5, 1779449699) == 1779449400
    assert interval_start(5, 1779449700) == 1779449700
    assert updown_slug("BTC", 5, 1779449400) == "btc-updown-5m-1779449400"


def test_venue_conformance_and_fee():
    v = PolymarketVenue(assets=["BTC"], intervals=[5])
    assert isinstance(v, ob.Venue)
    assert v.name == "polymarket"
    assert abs(v.fee(0.5, 100, is_maker=False) - 100 * CRYPTO_FEE_RATE * 0.25) < 1e-6
    assert v.fee(0.5, 100, is_maker=True) == 0.0


def test_live_executor_fails_closed_without_credentials():
    saved = os.environ.pop("POLYMARKET_PRIVATE_KEY", None)
    try:
        with pytest.raises(RuntimeError):
            PolymarketVenue(["BTC"], [5]).executor()
    finally:
        if saved is not None:
            os.environ["POLYMARKET_PRIVATE_KEY"] = saved


def test_live_gamma_discovery_is_lenient():
    try:
        markets = discover(["BTC"], [5])
    except (urllib.error.URLError, OSError):
        pytest.skip("Gamma API not reachable")
    for m in markets:  # may be empty between intervals; any returned must be well-formed
        assert m.venue == "polymarket"
        assert len(m.token_ids) >= 2


async def test_feed_survives_a_malformed_level_and_resubscribes_new_markets(monkeypatch):
    import json

    from oraclebook.adapters.polymarket import feed as feed_mod
    from oraclebook.core.bus import Bus
    from oraclebook.core.events import EVT_BOOK_UPDATE, EVT_MARKETS_DISCOVERED
    from oraclebook.core.models import Market

    bus = Bus()
    sub = bus.subscribe_raw(EVT_BOOK_UPDATE)
    discovered = bus.subscribe(EVT_MARKETS_DISCOVERED)
    f = feed_mod.PolymarketFeed(bus, assets=["btc"], intervals=[5])
    await f._on_message(json.dumps({"asset_id": "T", "bids": [{"price": "0.4", "size": "oops"}],
                                    "asks": [{"price": "0.6", "size": "10"}]}))
    assert sub.get_nowait().asks == ((0.6, 10.0),)  # bad level skipped, message kept

    sent = []

    class FakeWS:
        closed = False

        async def send_json(self, msg):
            sent.append(msg)

    rounds = iter([["a1", "a2"], ["a2", "b1", "b2"]])

    def fake_discover(assets, intervals):
        toks = next(rounds)
        return [Market(venue="polymarket", market_id=f"m-{t}", condition_id="c", token_ids=(t,))
                for t in toks]

    monkeypatch.setattr(feed_mod, "discover", fake_discover)
    await f._discover()
    f._ws = FakeWS()
    await f._discover()  # markets rolled: only the new tokens get subscribed
    assert sent == [{"operation": "subscribe", "assets_ids": ["b1", "b2"], "level": 2}]
    announced = [[m.market_id for m in discovered.get_nowait().markets] for _ in range(2)]
    assert announced == [["m-a1", "m-a2"], ["m-b1", "m-b2"]]  # each market announced once


def test_hourly_slug_uses_et_start_hour_across_dst():
    from oraclebook.adapters.polymarket.markets import hourly_slug, market_slug

    # 2026-11-10 05:30Z = 00:30 EST -> the 12am market
    assert hourly_slug("BTC", 1794288600) == "bitcoin-up-or-down-november-10-2026-12am-et"
    # 2026-07-01 16:10Z = 12:10 EDT -> the 12pm market (summer offset)
    assert hourly_slug("eth", 1782922200) == "ethereum-up-or-down-july-1-2026-12pm-et"
    assert hourly_slug("DOGE", 1794288600) == "dogecoin-up-or-down-november-10-2026-12am-et"
    assert hourly_slug("ZEC", 1794288600) is None
    assert market_slug("BTC", 5, 1779449400) == "btc-updown-5m-1779449400"


def test_market_parsing_reads_fee_schedule_and_v2_position_ids():
    from oraclebook.adapters.polymarket.markets import _to_market

    base = {"conditionId": "0xc", "id": "1", "slug": "s", "outcomes": '["Up","Down"]'}
    m = _to_market({**base, "clobTokenIds": '["a","b"]', "feeType": "crypto_fees_v2",
                    "feeSchedule": {"rate": 0.07, "exponent": 1, "takerOnly": True}})
    assert m.token_ids == ("a", "b") and m.fee_verified
    assert (m.start_ts, m.end_ts) == (None, None)
    timed = _to_market({**base, "clobTokenIds": '["a","b"]',
                        "eventStartTime": "2026-11-12T06:05:00Z", "endDate": "2026-11-12T06:10:00Z"})
    assert (timed.start_ts, timed.end_ts) == (1794463500.0, 1794463800.0)
    assert m.meta["fee_schedule"] == {"rate": 0.07, "exponent": 1.0}
    assert m.meta["version"] == "v1"

    # V2 ids are unverified against the socket/order API: skipped, not traded blind
    assert _to_market({**base, "version": "v2", "positionIds": ["p1", "p2"]}) is None
    assert _to_market({**base, "clobTokenIds": '["a","b"]', "feeSchedule": {"rate": None}}) \
        is not None  # a malformed schedule doesn't abort discovery
    off = _to_market({**base, "clobTokenIds": '["a","b"]', "feesEnabled": False,
                      "feeSchedule": {"rate": 0.07, "exponent": 1}})
    assert off.meta["fee_schedule"]["rate"] == 0.0

    assert _to_market({**base, "clobTokenIds": "[]"}) is None          # nothing to subscribe
    assert _to_market({**base, "clobTokenIds": '["a"]'}) is None       # ids/outcomes mismatch


async def test_feed_accepts_token_id_alias():
    import json

    from oraclebook.adapters.polymarket.feed import PolymarketFeed
    from oraclebook.core.bus import Bus
    from oraclebook.core.events import EVT_BOOK_UPDATE

    bus = Bus()
    sub = bus.subscribe_raw(EVT_BOOK_UPDATE)
    f = PolymarketFeed(bus, assets=[], intervals=[])
    await f._on_message(json.dumps({"token_id": "T", "bids": [{"price": "0.4", "size": "5"}],
                                    "asks": []}))
    assert sub.get_nowait().token_id == "T"
