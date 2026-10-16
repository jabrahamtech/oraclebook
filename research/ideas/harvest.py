"""Idea 6: post-event harvest. World Cup match markets (home / draw / away) have end_date at
kick-off and resolve ~2.3 h later; a match lasts ~1 h 55 min. Between the final whistle and
resolution the outcome is (usually) known but the market still trades. Buy, at the ask with fees,
any side priced in [lo, hi) at kick-off + k, while the market is still open, and hold to
resolution. Risk: matches that are not over (stoppage, delays) and resolution disputes, which the
real outcomes capture.

Usage: python -I harvest.py explore
       python -I harvest.py holdout --at K --lo X --hi Y
"""

import argparse
import pathlib
import sys

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common as C

TD = pd.Timedelta
OFFSETS = ["90min", "105min", "115min", "120min", "125min", "130min", "135min", "145min"]


def load():
    m, cut = C.markets("worldcup")
    n = m.groupby("event_id").mkey.transform("size")
    m = m[n == 3]
    snap = C.P.sample(
        m,
        C.quotes("worldcup", columns=["mkey", "token", "ts", "bid", "ask"]),
        "close_ts",
        {o: TD(o) for o in OFFSETS},
        TD("5min"),
        before="resolved_ts",
        tokens=(0,),
    )
    c = C.P.contracts(snap, max_spread=0.05)
    c["fee_paid"] = C.poly_fee(c.p_buy)
    c["ret"] = (c.y - c.p_buy - c.fee_paid) / (c.p_buy + c.fee_paid)
    c["price"] = c.p_buy
    c["cluster"] = c.event_id
    c["mins_to_resolution"] = (c.resolved_ts - c.t).dt.total_seconds() / 60
    return c, cut


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["explore", "holdout"])
    ap.add_argument("--at")
    ap.add_argument("--lo", type=float)
    ap.add_argument("--hi", type=float)
    ap.add_argument("--alpha", type=float, default=0.05)
    a = ap.parse_args()
    c, cut = load()
    print(f"worldcup match markets: split at close {cut}")
    if a.mode == "holdout":
        t = c[(c.split == "holdout") & (c.horizon == a.at) & (c.p_buy >= a.lo) & (c.p_buy < a.hi)]
        print(
            f"HOLDOUT at kickoff+{a.at} buy ask in [{a.lo},{a.hi}): "
            f"{C.fmt(C.summarise(t, alpha=a.alpha))}"
        )
        return
    e = c[c.split == "explore"]
    rows = []
    for o in OFFSETS:
        for lo, hi in ((0.80, 0.90), (0.90, 0.95), (0.95, 0.98), (0.98, 0.995), (0.995, 1.0)):
            t = e[(e.horizon == o) & (e.p_buy >= lo) & (e.p_buy < hi)]
            s = C.summarise(t)
            rows.append(
                {
                    "at": o,
                    "ask_band": f"[{lo},{hi})",
                    **s,
                    "losers": int((t.y == 0).sum()),
                    "median_min_to_res": t.mins_to_resolution.median(),
                }
            )
    print("exploration: buy at the ask (fee 0.07 p(1-p), spread <= 5c), hold to resolution")
    print(pd.DataFrame(rows).to_string(index=False, float_format=lambda v: f"{v:.4f}"))


if __name__ == "__main__":
    main()
