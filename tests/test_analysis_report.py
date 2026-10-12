"""`oraclebook replay --report`: per-market fills / notional / fees / markouts, deterministic."""
import json
import os
import tempfile

import oraclebook as ob
from oraclebook.cli import main


class BidBuyer(ob.BaseStrategy):
    name = "bb"

    async def on_book(self, book):
        if 0 < book.best_bid <= 0.40:
            return [
                ob.OrderRequest(
                    token_id=book.token_id,
                    market_id=book.market_id,
                    side=ob.Side.BUY,
                    price=book.best_bid,
                    size=10,
                    order_type=ob.OrderType.LIMIT,
                    strategy=self.name,
                )
            ]
        return []


def _write_recording(path):
    # (ts, token, market, bid, ask); fills trigger on bids <= 0.40.
    rows = [
        (1000.0, "UP", "m2", 0.50, 0.52),
        (1001.0, "UP", "m2", 0.38, 0.40),
        (1002.0, "A", "m1", 0.35, 0.37),
        (1003.0, "UP", "m2", 0.45, 0.47),
        (1015.0, "A", "m1", 0.50, 0.52),
        (1030.0, "UP", "m2", 0.60, 0.62),
        (1040.0, "A", "m1", 0.20, 0.22),
        (1100.0, "UP", "m2", 0.70, 0.72),
    ]
    with open(path, "w", encoding="utf-8") as f:
        for seq, (ts, token, market, bid, ask) in enumerate(rows):
            payload = {"id": f"e{seq}", "ts": ts, "token_id": token, "market_id": market,
                       "best_bid": bid, "best_ask": ask, "bids": [], "asks": [], "age_ms": 0.0}
            rec = {"seq": seq, "topic": "book_update", "ts": ts, "type": "BookUpdate",
                   "payload": payload}
            f.write(json.dumps(rec) + "\n")


def _run(capsys, path):
    exit_code = main(
        ["replay", "--strategy", "test_analysis_report:BidBuyer", "--from", path, "--report"]
    )
    assert exit_code == 0
    return capsys.readouterr().out


def test_report_prints_per_market_markouts(capsys):
    path = os.path.join(tempfile.mkdtemp(), "rec.jsonl")
    _write_recording(path)
    out = _run(capsys, path)
    assert "Replay report" in out
    assert "fills:      3" in out
    assert "numbers only — not a profitability claim" in out
    lines = out.splitlines()
    header = next(line for line in lines if line.strip().startswith("market"))
    assert "mo_1s" in header and "mo_10s" in header and "mo_60s" in header
    m1 = next(line for line in lines if line.strip().startswith("m1 "))
    m2 = next(line for line in lines if line.strip().startswith("m2 "))
    assert lines.index(m1) < lines.index(m2)  # markets sorted
    # m1: A bought @0.35 (t=1002) and @0.20 (t=1040); m2: UP bought @0.38 (t=1001).
    # m2 mids as of +1/+10/+60s: 0.39 / 0.46 / 0.61 -> markouts +0.01 / +0.08 / +0.23.
    assert m2.split() == ["m2", "1", "3.80", "0.0000", "+0.0100", "+0.0800", "+0.2300", "n/a"]
    # m1: A's mid series ends at t=1040, so only the first fill has a 10s mid, none a 60s one.
    assert m1.split() == ["m1", "2", "5.50", "0.0000", "+0.0100", "+0.0100", "n/a", "n/a"]


def test_report_is_deterministic(capsys):
    path = os.path.join(tempfile.mkdtemp(), "rec.jsonl")
    _write_recording(path)
    assert _run(capsys, path) == _run(capsys, path)
