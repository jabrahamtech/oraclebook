"""CompleteSetMaker under Replay: with trade-through fills, the honest failure mode shows up —
the leg that fills is the one the market moved against, and the set is never completed."""
import json

from oraclebook.replay import run_replay
from oraclebook.strategies.complete_set_maker import CompleteSetMaker


def _write(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for seq, (ts, token, bid, ask) in enumerate(rows):
            payload = {"ts": ts, "token_id": token, "market_id": "m", "best_bid": bid,
                       "best_ask": ask}
            f.write(json.dumps({"seq": seq, "topic": "book_update", "ts": ts,
                                "type": "BookUpdate", "payload": payload}) + "\n")


async def test_one_leg_adverse_selection_is_visible_in_replay(tmp_path):
    rec = tmp_path / "rec.jsonl"
    _write(rec, [
        (1.0, "UP", 0.45, 0.50),
        (1.5, "DOWN", 0.45, 0.50),   # edge 0.10 -> quote both legs at 0.45
        (2.0, "UP", 0.30, 0.35),     # UP collapses through our bid: we buy it at 0.45
        (2.5, "DOWN", 0.60, 0.65),   # DOWN rallies away: our 0.45 bid never trades
    ])
    strategy = CompleteSetMaker(min_edge=0.02, size=10)
    app = await run_replay(str(rec), strategy, settlements={"UP": 0.0, "DOWN": 1.0})
    fills = [f["payload"] for f in app.ledger.fills()]
    assert [(f["token_id"], f["price"], f["fill_source"]) for f in fills] == [
        ("UP", 0.45, "paper_maker")
    ]
    assert strategy.unhedged("m") == 10
    assert abs(app.book.realized_pnl - (-4.5)) < 1e-9   # paid 0.45 x 10 for the losing leg
