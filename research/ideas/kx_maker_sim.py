"""Round 2: queue-aware maker check for short Kalshi markets. At t_post (minutes before close) the
YES bid / ask are the most recent taker-sell / taker-buy prints in the previous `look` minutes.
Rest a bid at the bid (buys YES) and an offer at the ask (buys NO at 1 - ask) when the mid is in
[lo, hi]; a quote fills only if a later trade before t_end (minutes before close) prints through
it. Filled quotes are held to settlement; maker fee per series. Fills therefore come with the
adverse selection a back-of-queue order would see.

Usage: python -I kx_maker_sim.py <series,...> explore
       python -I kx_maker_sim.py <series,...> holdout --post P --end E [--lo X --hi Y]
"""

import argparse
import pathlib
import sys

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import kx


def sim(t, post, end, look=1.0, lo=0.10, hi=0.90):
    t = t.assign(left=(t.close_time - t.ts).dt.total_seconds() / 60)
    before = t[(t.left >= post) & (t.left < post + look)]
    q = before.sort_values("ts").groupby(["ticker", "taker_side"]).yes_price.last().unstack()
    q = q.reindex(columns=["yes", "no"]).rename(columns={"yes": "ask", "no": "bid"})
    after = t[(t.left < post) & (t.left >= end)]
    ext = after.groupby("ticker").yes_price.agg(lo_after="min", hi_after="max")
    meta = t.drop_duplicates("ticker").set_index("ticker")[
        ["y", "fee_maker", "event_ticker", "split"]
    ]
    g = q.join(ext, how="inner").join(meta)
    g = g[(g.bid < g.ask) & ((g.bid + g.ask) / 2).between(lo, hi)]
    rows = []
    for side in ("yes", "no"):
        if side == "yes":
            f = g[g.lo_after < g.bid].assign(price=lambda d: d.bid, yy=lambda d: d.y)
        else:
            f = g[g.hi_after > g.ask].assign(price=lambda d: 1 - d.ask, yy=lambda d: 1 - d.y)
        fee = f.fee_maker * f.price * (1 - f.price)
        rows.append(f.assign(side=side, count=1.0, cost=f.price + fee, pnl=f.yy - f.price - fee))
    return len(g), pd.concat(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("series")
    ap.add_argument("mode", choices=["explore", "holdout"])
    ap.add_argument("--post", type=float)
    ap.add_argument("--end", type=float)
    ap.add_argument("--lo", type=float, default=0.10)
    ap.add_argument("--hi", type=float, default=0.90)
    ap.add_argument("--alpha", type=float, default=0.05)
    a = ap.parse_args()
    _m, t = kx.load(a.series.split(","))
    if a.mode == "holdout":
        n, f = sim(t[t.split == "holdout"], a.post, a.end, lo=a.lo, hi=a.hi)
        print(
            f"HOLDOUT maker sim {a.series} post {a.post} end {a.end}: "
            f"quotes {2 * n}, fills {len(f)}, "
            f"{kx.fmt(kx.ratio_boot(f, 'event_ticker', alpha=a.alpha))}"
        )
        return
    e = t[t.split == "explore"]
    for post, end in ((14, 10), (14, 5), (12, 8), (10, 5), (8, 3), (5, 1)):
        n, f = sim(e, post, end)
        print(
            f"post {post:>2} min left, cancel at {end:>2}: quotes {2 * n}, fills {len(f)} "
            f"({len(f) / max(2 * n, 1):.2f}): {kx.fmt(kx.ratio_boot(f, 'event_ticker'))}"
        )


if __name__ == "__main__":
    main()
