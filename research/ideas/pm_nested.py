"""Idea P4 (PROTOCOL_V6.md): nested expiries in Polymarket crypto Up/Down. A 15-minute market and
its last 5-minute market (or a 4-hour market and its last 15-/5-minute markets) settle on the same
close with different strikes. With the shorter market's strike K_s above the longer's K_l,
Up(short) implies Up(long), so Up(long) + Down(short) pays $1, or $2 when the close lands between
the strikes; mirrored when K_s < K_l. Strikes are the Binance 1-minute open at each window start;
pairs with |ln(K_s / K_l)| < 3 bp are skipped.

Descriptive: share of seconds where the pair costs < $1 after both taker fees, and how long those
episodes last. Rule P4(e): at the first second with cost <= 1 - e (both books known within 1 s,
spreads <= 5c), buy both legs at the asks of their next quotes (within 2 s), hold to resolution.

Usage: python -I pm_nested.py explore
       python -I pm_nested.py holdout --edge E --alpha 0.01   (frozen rule, run once)
"""

import argparse
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common as C

TD = pd.Timedelta
LENGTHS = {"5m": TD("5min"), "15m": TD("15min"), "4h": TD("4h")}
EDGES = (0.0, 0.01)
MIN_GAP = 3e-4
MAX_SPREAD = 0.05


def pairs(m):
    """(long, short) market pairs sharing a coin and a close, with strikes from Binance."""
    m = m.assign(open_ts=m.close_ts - m.length.map(LENGTHS))
    strikes = []
    for coin, g in m.groupby("coin"):
        k = C.klines(coin).set_index("start").open
        strikes.append(g.open_ts.map(k))
    m["strike"] = pd.concat(strikes)
    x = m.merge(m, on=["coin", "close_ts"], suffixes=("_l", "_s"))
    x = x[x.length_l.map(LENGTHS) > x.length_s.map(LENGTHS)]
    x["gap"] = np.log(x.strike_s / x.strike_l)
    return x[x.gap.abs() >= MIN_GAP].reset_index(drop=True)


def book(m):
    """Tight quotes per (mkey, token), time-sorted."""
    q = pd.concat(C.quotes("polyorderbooks", columns=["mkey", "token", "ts", "bid", "ask"]))
    q = q[(q.bid > 0) & (q.ask < 1) & (q.bid <= q.ask) & (q.ask - q.bid <= MAX_SPREAD)]
    q = q[q.mkey.isin(m.mkey)].sort_values("ts")
    return {k: g[["ts", "ask"]].reset_index(drop=True) for k, g in q.groupby(["mkey", "token"])}


def legs(p):
    """Leg A / leg B (mkey, token) and the payoff of the pair that pays >= $1."""
    hi = p.gap > 0  # short strike above long strike: Up(long) + Down(short)
    return p.assign(
        mkey_a=np.where(hi, p.mkey_l, p.mkey_s), tok_a=np.int8(0),
        mkey_b=np.where(hi, p.mkey_s, p.mkey_l), tok_b=np.int8(1),
        payoff=np.where(hi, p.y_l + 1 - p.y_s, p.y_s + 1 - p.y_l),
        start=p.close_ts - p.length_s.map(LENGTHS),
    )


def costs(p, q):
    """Per pair, every second where both legs are quoted: the cost of the pair after fees."""
    out = []
    for r in p.itertuples():
        a = q.get((r.mkey_a, r.tok_a))
        b = q.get((r.mkey_b, r.tok_b))
        if a is None or b is None:
            continue
        a = a[(a.ts > r.start) & (a.ts < r.close_ts - TD("10s"))]
        x = pd.merge_asof(a, b.rename(columns={"ts": "ts_b"}), left_on="ts",
                          right_on="ts_b", direction="backward", tolerance=TD("1s"),
                          suffixes=("_a", "_b")).dropna(subset=["ask_b"])
        cost = x.ask_a + x.ask_b + C.poly_fee(x.ask_a) + C.poly_fee(x.ask_b)
        out.append(pd.DataFrame({"pair": r.Index, "ts": x.ts, "cost": cost}))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=["pair", "ts", "cost"])


def episodes(c):
    """Runs of consecutive quoted seconds (gap <= 2 s) with cost < 1: count and duration."""
    c = c.sort_values(["pair", "ts"])
    under = c.cost < 1
    new = under & ~(under.shift(fill_value=False) & (c.pair == c.pair.shift())
                    & (c.ts - c.ts.shift() <= TD("2s")))
    run = new.cumsum().where(under)
    d = c[under].groupby(run[under]).ts.agg(lambda s: (s.max() - s.min()).total_seconds() + 1)
    if not len(d):
        return f"{len(c):,} s quoted, none under $1"
    return (f"{len(c):,} s quoted, {under.mean():.2%} under $1, {len(d)} episodes "
            f"(median {d.median():.0f} s, max {d.max():.0f} s)")


def trades(p, q, c, edge):
    hit = c[c.cost <= 1 - edge].sort_values("ts").drop_duplicates("pair")
    rows = []
    for h in hit.itertuples():
        r = p.loc[h.pair]
        asks = []
        for mk, tok in ((r.mkey_a, r.tok_a), (r.mkey_b, r.tok_b)):
            g = q.get((mk, tok))
            nxt = g[(g.ts > h.ts) & (g.ts <= h.ts + TD("2s"))] if g is not None else g
            asks.append(nxt.ask.iloc[0] if nxt is not None and len(nxt) else np.nan)
        if np.isnan(asks).any():
            continue
        cost = sum(asks) + sum(C.poly_fee(x) for x in asks)
        rows.append({"cluster": r.event_id_l, "price": cost, "y": r.payoff,
                     "ret": (r.payoff - cost) / cost})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["explore", "holdout"])
    ap.add_argument("--edge", type=float)
    ap.add_argument("--alpha", type=float, default=0.05)
    a = ap.parse_args()
    m, cut = C.markets("polyorderbooks")
    p = legs(pairs(m))
    q = book(m)
    print(f"P4: {len(p)} nested pairs (strike gap >= 3 bp), split {cut:%Y-%m-%d}")
    split = "explore" if a.mode == "explore" else "holdout"
    ps = p[p.split_l == split]
    c = costs(ps, q)
    if a.mode == "explore":
        for (ll, ls), g in ps.groupby(["length_l", "length_s"]):
            print(f"  {ll} vs last {ls}: {len(g)} pairs, {episodes(c[c.pair.isin(g.index)])}")
        for e in EDGES:
            print(f"    cost<=1-{e}: {C.fmt(C.summarise(trades(ps, q, c, e)))}")
        return
    print(f"HOLDOUT edge={a.edge} alpha={a.alpha}: "
          f"{C.fmt(C.summarise(trades(ps, q, c, a.edge), alpha=a.alpha))}")


if __name__ == "__main__":
    main()
