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
from oraclebook.adapters.polymarket.markets import interval_start


def test_fee_peaks_at_midpoint_and_maker_is_free():
    assert abs(taker_fee_per_share(0.5) - 0.072 * 0.25) < 1e-9  # 0.018/share at p=0.5
    assert taker_fee_per_share(0.5) > taker_fee_per_share(0.10)
    assert taker_fee_per_share(0.5) > taker_fee_per_share(0.90)
    assert fee_amount(0.5, 100, is_maker=True) == 0.0
    assert abs(fee_amount(0.5, 100) - 1.8) < 1e-6


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
    assert abs(v.fee(0.5, 100, is_maker=False) - 1.8) < 1e-6
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
    from oraclebook.core.events import EVT_BOOK_UPDATE
    from oraclebook.core.models import Market

    bus = Bus()
    sub = bus.subscribe_raw(EVT_BOOK_UPDATE)
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
