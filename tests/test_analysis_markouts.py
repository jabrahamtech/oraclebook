"""Markouts: as-of mid lookup (no look-ahead), None past the series end, sign conventions."""
import json
import os
import tempfile

import pytest

from oraclebook.analysis import markouts, mid_series_from_recording, summarize
from oraclebook.core.models import Fill, Side

# Scripted mid path for token T: (ts, mid).
MIDS = {"T": [(100.0, 0.50), (101.0, 0.52), (105.0, 0.55), (130.0, 0.60), (200.0, 0.40)]}


def _ledger_fill(side, price, size, ts, fee=0.0, token="T", market="m1"):
    payload = {"order_id": "o", "token_id": token, "market_id": market, "side": side,
               "price": price, "size": size, "fee": fee, "ts": ts}
    return {"topic": "fill", "ts": ts, "payload": payload}


def test_buy_markouts_use_as_of_mids():
    [m] = markouts([_ledger_fill("BUY", 0.49, 10, 100.5, fee=0.1)], MIDS)
    assert m.mid_at_fill == 0.50  # the 0.52 mid at ts=101 is in the future, never used
    assert m.effective_spread == pytest.approx(0.01)
    assert m.markout[1.0] == pytest.approx(0.03)  # mid as of 101.5 -> 0.52
    assert m.markout[10.0] == pytest.approx(0.06)  # as of 110.5 -> 0.55
    assert m.markout[60.0] == pytest.approx(0.11)  # as of 160.5 -> 0.60
    assert m.adverse_selection[1.0] == pytest.approx(0.02)
    assert m.realized_spread[1.0] == pytest.approx(-0.01)
    assert m.markout_usd[1.0] == pytest.approx(0.30)
    assert m.effective_spread_usd == pytest.approx(0.10)
    assert m.fee == 0.1
    assert m.to_resolution is None


def test_sell_markouts_sign_and_none_past_series_end():
    [m] = markouts([_ledger_fill("SELL", 0.62, 5, 190.0)], MIDS, settlements={"T": 1.0})
    assert m.mid_at_fill == 0.60
    assert m.effective_spread == pytest.approx(0.02)
    assert m.markout[1.0] == pytest.approx(0.02)
    assert m.adverse_selection[1.0] == pytest.approx(0.0)
    # ts + 10 == 200 exactly: the mid stamped at 200 is "at or before", so it counts.
    assert m.markout[10.0] == pytest.approx(0.22)
    assert m.adverse_selection[10.0] == pytest.approx(0.20)
    assert m.realized_spread[10.0] == pytest.approx(-0.18)
    # ts + 60 = 250 is past the last recorded mid (200): no extrapolation.
    assert m.markout[60.0] is None
    assert m.adverse_selection[60.0] is None
    assert m.realized_spread[60.0] is None
    assert m.to_resolution == pytest.approx(-0.38)
    assert m.to_resolution_usd == pytest.approx(-1.90)


def test_fill_before_first_mid_has_no_reference_mid():
    [m] = markouts([_ledger_fill("BUY", 0.45, 1, 99.0)], MIDS)
    assert m.mid_at_fill is None
    assert m.effective_spread is None
    assert m.markout[1.0] == pytest.approx(0.05)  # as of 100 -> 0.50
    assert m.adverse_selection[1.0] is None
    assert m.realized_spread[1.0] is None


def test_unknown_token_gives_all_none():
    [m] = markouts([_ledger_fill("BUY", 0.45, 1, 150.0, token="X")], MIDS)
    assert m.mid_at_fill is None
    assert all(v is None for v in m.markout.values())


def test_no_look_ahead_future_mids_do_not_change_results():
    fill = _ledger_fill("BUY", 0.49, 10, 100.5)
    base = markouts([fill], MIDS)[0]
    # Rewrite only mids stamped after ts + 60 (the longest horizon): nothing may change.
    perturbed = {"T": MIDS["T"][:-1] + [(161.0, 0.99), (200.0, 0.01)]}
    other = markouts([fill], perturbed)[0]
    assert other.mid_at_fill == base.mid_at_fill
    assert other.markout == base.markout
    # A mid an instant after the fill must not leak into m0.
    nudged = {"T": [(100.0, 0.50), (100.5000001, 0.90)] + MIDS["T"][1:]}
    assert markouts([fill], nudged)[0].mid_at_fill == 0.50


def test_fill_objects_and_ledger_dicts_agree():
    obj = Fill(order_id="o", token_id="T", market_id="m1", side=Side.BUY, price=0.49, size=10,
               fee=0.1, ts=100.5)
    a = markouts([obj], MIDS, settlements={"T": 0.0})[0]
    b = markouts([_ledger_fill("BUY", 0.49, 10, 100.5, fee=0.1)], MIDS, settlements={"T": 0.0})[0]
    assert a == b
    assert a.pnl_to_resolution_net == pytest.approx(-0.49 * 10 - 0.1)


def test_custom_horizons():
    [m] = markouts([_ledger_fill("BUY", 0.49, 10, 100.5)], MIDS, horizons=(0.25, 4.5))
    assert set(m.markout) == {0.25, 4.5}
    assert m.markout[0.25] == pytest.approx(0.01)  # as of 100.75 -> still 0.50
    assert m.markout[4.5] == pytest.approx(0.06)  # as of 105.0 -> 0.55


def test_summarize_size_weighted_and_net_of_fees():
    fills = [
        _ledger_fill("BUY", 0.49, 10, 100.5, fee=0.1, market="m1"),
        _ledger_fill("SELL", 0.62, 5, 190.0, fee=0.05, market="m1"),
        _ledger_fill("BUY", 0.45, 2, 150.0, fee=0.0, token="U", market="m0"),
    ]
    rows = markouts(fills, MIDS, settlements={"T": 1.0})
    summary = summarize(rows)
    assert list(summary) == ["m0", "m1"]  # sorted, deterministic
    m1 = summary["m1"]
    assert m1.count == 2
    assert m1.size == 15
    assert m1.notional == pytest.approx(0.49 * 10 + 0.62 * 5)
    assert m1.fees == pytest.approx(0.15)
    assert m1.mean_markout[1.0] == pytest.approx((0.03 * 10 + 0.02 * 5) / 15)
    assert m1.mean_markout[60.0] == pytest.approx(0.11)  # only the BUY reaches 60s
    assert m1.markout_count == {1.0: 2, 10.0: 2, 60.0: 1}
    assert m1.pnl_to_resolution == pytest.approx(0.51 * 10 - 0.38 * 5 - 0.15)
    m0 = summary["m0"]
    assert m0.mean_markout[1.0] is None
    assert m0.pnl_to_resolution is None  # token U never settled


def test_summarize_by_token():
    rows = markouts([_ledger_fill("BUY", 0.49, 1, 100.5), _ledger_fill("BUY", 0.4, 1, 100.5,
                                                                        token="U")], MIDS)
    assert list(summarize(rows, by="token_id")) == ["T", "U"]


def test_mid_series_from_recording():
    path = os.path.join(tempfile.mkdtemp(), "rec.jsonl")
    recs = [
        {"seq": 2, "topic": "book_update", "ts": 3.0, "type": "BookUpdate",
         "payload": {"token_id": "T", "market_id": "m1", "best_bid": 0.4, "best_ask": 0.6}},
        {"seq": 0, "topic": "book_update", "ts": 1.0, "type": "BookUpdate",
         "payload": {"token_id": "T", "market_id": "m1", "best_bid": 0.5, "best_ask": 0.52}},
        {"seq": 1, "topic": "book_update", "ts": 2.0, "type": "BookUpdate",
         "payload": {"token_id": "T", "market_id": "m1", "best_bid": 0.5, "best_ask": 0.0}},
        {"seq": 3, "topic": "price_tick", "ts": 4.0, "type": "PriceTick",
         "payload": {"symbol": "BTC", "price": 1.0}},
        {"seq": 4, "topic": "book_update", "ts": 5.0, "type": "BookUpdate",
         "payload": {"token_id": "U", "market_id": "m1", "best_bid": 0.1, "best_ask": 0.2}},
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(r) + "\n" for r in recs)
        f.write("\n")
    series = mid_series_from_recording(path)
    assert series["T"] == [(1.0, pytest.approx(0.51)), (3.0, pytest.approx(0.5))]
    assert series["U"] == [(5.0, pytest.approx(0.15))]
    assert set(series) == {"T", "U"}


def test_unknown_side_rejected():
    with pytest.raises(ValueError):
        markouts([_ledger_fill("HOLD", 0.5, 1, 100.0)], MIDS)
