"""Polymarket adapter: fee model, Gamma market mapping, interval slugs, Venue conformance
(offline), plus a lenient live-Gamma discovery check (skipped if the API is unreachable)."""
import os
import urllib.error

import pytest

import oraclebook as ob
from oraclebook.adapters.polymarket import (
    PolymarketVenue,
    discover,
    fee_amount,
    fee_for_market,
    taker_fee_per_share,
    token_for_outcome,
    updown_slug,
)
from oraclebook.adapters.polymarket.markets import _to_market, interval_start

GAMMA = {"conditionId": "0xc", "id": "1", "slug": "btc-updown-5m-1",
         "clobTokenIds": '["tok-down","tok-up"]', "outcomes": '["Down","Up"]',
         "orderPriceMinTickSize": 0.001, "orderMinSize": 5, "negRisk": True,
         "feesEnabled": True,
         "feeSchedule": {"rate": "0.05", "exponent": 2, "takerOnly": True, "rebateRate": "0.2"},
         "endDate": "2026-05-22T12:00:00Z"}


def test_fee_peaks_at_midpoint_and_maker_is_free():
    assert abs(taker_fee_per_share(0.5) - 0.072 * 0.25) < 1e-9  # 0.018/share at p=0.5
    assert taker_fee_per_share(0.5) > taker_fee_per_share(0.10)
    assert taker_fee_per_share(0.5) > taker_fee_per_share(0.90)
    assert fee_amount(0.5, 100, is_maker=True) == 0.0
    assert abs(fee_amount(0.5, 100) - 1.8) < 1e-6


def test_gamma_fee_schedule_and_trading_params_are_read():
    m = _to_market(GAMMA)
    assert m.fee_verified and m.fee_taker_only
    assert m.meta["fee_rate"] == 0.05 and m.meta["fee_exponent"] == 2.0
    assert m.meta["fee_rebate_rate"] == 0.2
    assert m.meta["neg_risk"] is True
    assert m.tick_size == "0.001" and m.min_order_size == 5.0
    assert m.end_ts == 1779451200.0


def test_tokens_map_to_outcomes_by_label_not_position():
    m = _to_market(GAMMA)
    assert token_for_outcome(m, "up") == "tok-up"
    assert token_for_outcome(m, "Down") == "tok-down"
    assert token_for_outcome(m, "Yes") is None


def test_mismatched_outcomes_and_tokens_are_rejected():
    assert _to_market(dict(GAMMA, outcomes='["Up"]')) is None


def test_fees_disabled_is_verified_zero_and_missing_schedule_is_unverified():
    free = _to_market(dict(GAMMA, feesEnabled=False))
    assert free.fee_verified and fee_for_market(free, 0.5, 100, is_maker=False) == 0.0
    unknown = _to_market({k: v for k, v in GAMMA.items() if k != "feeSchedule"})
    assert not unknown.fee_verified
    assert abs(fee_for_market(unknown, 0.5, 100, is_maker=False) - 1.8) < 1e-9  # crypto default


def test_fee_for_market_uses_market_schedule():
    m = _to_market(GAMMA)
    assert abs(fee_for_market(m, 0.5, 100, is_maker=False) - 0.05 * 0.25**2 * 100) < 1e-12
    assert fee_for_market(m, 0.5, 100, is_maker=True) == 0.0
    maker_pays = _to_market(dict(GAMMA, feeSchedule={**GAMMA["feeSchedule"], "takerOnly": False}))
    assert fee_for_market(maker_pays, 0.5, 100, is_maker=True) > 0.0


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
