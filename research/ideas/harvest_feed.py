"""Idea 6b (descriptive): harvest with a live score feed. For each World Cup match in the sports
feed, at the first `ended` update + latency, buy the side the final score implies in each of the
match's three markets (YES on the market the score says won, NO on the other two) at the current
ask, with fees, and hold to resolution. 68 matches only, so this is a description, not a test.

Usage: python -I harvest_feed.py [--latency-min 1]
"""

import argparse
import pathlib
import sys

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common as C

ALIASES = {"IR Iran": "Iran", "Korea Republic": "Korea Republic", "USA": "United States"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--latency-min", type=float, default=1.0)
    a = ap.parse_args()
    root = next(C.RAW.glob("kaggle_madrading__*"))
    s = pd.read_parquet(root / "sports_state.parquet")
    end = s[s.ended].sort_values("ts_recv_ns").groupby("game_id").head(1).copy()
    end["t_end"] = pd.to_datetime(end.ts_recv_ns, unit="ns", utc=True)
    m, _cut = C.markets("worldcup")
    n = m.groupby("event_id").mkey.transform("size")
    m = m[n == 3]
    rows = []
    for g in end.itertuples():
        home, away = (ALIASES.get(x, x) for x in (g.home_team, g.away_team))
        cand = m[(m.close_ts < g.t_end) & (m.close_ts > g.t_end - pd.Timedelta("4h"))]
        cand = cand[
            cand.question.str.contains(home, regex=False)
            | cand.question.str.contains(away, regex=False)
        ]
        ev = cand.event_id.value_counts()
        if ev.empty or ev.iloc[0] != 3:
            continue
        mk = cand[cand.event_id == ev.index[0]]
        hs, as_ = (int(x) for x in g.score.split("-"))
        result = "draw" if hs == as_ else (home if hs > as_ else away)
        t = g.t_end + pd.Timedelta(minutes=a.latency_min)
        snap = C.P.sample(
            mk,
            C.quotes("worldcup", columns=["mkey", "token", "ts", "bid", "ask"]),
            pd.Series(t, index=mk.index),
            {"x": pd.Timedelta(0)},
            pd.Timedelta("10min"),
            before="resolved_ts",
            tokens=(0,),
        )
        for r in snap.itertuples():
            says_yes = (
                ("draw" in r.question.lower())
                if result == "draw"
                else (f"Will {result} win" in r.question)
            )
            price = r.ask if says_yes else 1 - r.bid
            y = r.y if says_yes else 1 - r.y
            if not 0 < price < 1:
                continue
            fee = C.poly_fee(price)
            rows.append(
                {
                    "game": f"{home}-{away} {g.score}",
                    "question": r.question,
                    "buy": "YES" if says_yes else "NO",
                    "price": price,
                    "y": y,
                    "ret": (y - price - fee) / (price + fee),
                    "mins_to_res": (r.resolved_ts - t).total_seconds() / 60,
                    "cluster": r.event_id,
                }
            )
    d = pd.DataFrame(rows)
    print(f"matched games: {d.game.nunique()} of {len(end)}; contracts {len(d)}")
    print(d.price.describe(percentiles=[0.1, 0.25, 0.5, 0.75]).round(4).to_dict())
    print(f"all: {C.fmt(C.summarise(d))}; losers {int((d.y == 0).sum())}")
    print(
        d.sort_values("price")
        .head(8)[["game", "question", "buy", "price", "y", "mins_to_res"]]
        .to_string(index=False)
    )


if __name__ == "__main__":
    main()
