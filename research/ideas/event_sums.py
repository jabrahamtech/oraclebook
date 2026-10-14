"""Idea 2b: mutually exclusive outcomes priced inconsistently. In a World Cup match event with three
markets (home win / draw / away win; exactly one resolves YES), buying all three YES at the asks
pays $1 for sum(ask) + fees; buying all three NO (at 1 - bid each) pays $2 for 3 - sum(bid) +
fees. Either is a locked-in gain when the prices allow it. Books are the minute-end best quotes;
a market's book persists until its next bar, carried forward at most `--max-age` minutes.

Usage: python -I event_sums.py [--max-age 30]
"""

import argparse
import pathlib
import sys

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common as C


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-age", type=int, default=30)
    ap.add_argument("--legs", type=int, default=3)
    ap.add_argument("--latency", type=int, default=0, help="minutes between seeing and trading")
    ap.add_argument("--min-gain", type=float, default=0.0)
    ap.add_argument("--holdout", action="store_true", help="report the holdout (frozen rule only)")
    a = ap.parse_args()
    m, cut = C.markets("worldcup")
    n = m.groupby("event_id").mkey.transform("size")
    m = m[n == a.legs]
    yes = m.groupby("event_id").y.sum()
    print(
        f"{m.event_id.nunique()} events with {a.legs} markets; exactly one YES in "
        f"{(yes == 1).mean():.3f} of them; split at close {cut}"
    )
    q = pd.concat(
        x[x.mkey.isin(m.mkey)] for x in C.quotes("worldcup", columns=["mkey", "ts", "bid", "ask"])
    )
    q = q.merge(m[["mkey", "event_id", "split", "close_ts", "resolved_ts", "y"]], on="mkey")
    q = q[q.ts < q.resolved_ts]
    rows = []
    for ev, g in q.groupby("event_id"):
        bid = g.pivot_table(index="ts", columns="mkey", values="bid", aggfunc="last")
        ask = g.pivot_table(index="ts", columns="mkey", values="ask", aggfunc="last")
        if bid.shape[1] != a.legs:
            continue
        grid = pd.date_range(bid.index.min(), bid.index.max(), freq="1min")
        bid = bid.reindex(grid).ffill(limit=a.max_age)
        ask = ask.reindex(grid).ffill(limit=a.max_age)
        ok = (ask.gt(0) & ask.lt(1) & bid.gt(0) & bid.lt(1) & bid.le(ask)).all(axis=1)
        bid, ask = bid[ok], ask[ok]
        if bid.empty:
            continue
        fee_y = C.poly_fee(ask).sum(axis=1)
        fee_n = C.poly_fee(1 - bid).sum(axis=1)
        gain_yes = 1 - ask.sum(axis=1) - fee_y
        gain_no = bid.sum(axis=1) - 1 - fee_n
        cost_yes = ask.sum(axis=1) + fee_y
        cost_no = a.legs - bid.sum(axis=1) + fee_n
        if a.latency:
            rows.append(simulate(ev, g, bid, ask, gain_yes, gain_no, a))
            continue
        rows.append(
            pd.DataFrame(
                {
                    "event_id": ev,
                    "t": bid.index,
                    "split": g.split.iloc[0],
                    "sum_ask": ask.sum(axis=1).to_numpy(),
                    "sum_bid": bid.sum(axis=1).to_numpy(),
                    "gain_yes": gain_yes.to_numpy(),
                    "ret_yes": (gain_yes / cost_yes).to_numpy(),
                    "gain_no": gain_no.to_numpy(),
                    "ret_no": (gain_no / cost_no).to_numpy(),
                }
            )
        )
    d = pd.concat(rows, ignore_index=True)
    if a.latency:
        report(d, a)
        return
    print(
        f"{len(d):,} event-minutes with all {a.legs} books two-sided, {d.event_id.nunique()} events"
    )
    print(
        "sum of asks:", d.sum_ask.describe(percentiles=[0.001, 0.01, 0.1, 0.5]).round(4).to_dict()
    )
    print(
        "sum of bids:", d.sum_bid.describe(percentiles=[0.5, 0.9, 0.99, 0.999]).round(4).to_dict()
    )
    for side in ("yes", "no"):
        hit = d[d[f"gain_{side}"] > 0]
        first = hit.sort_values("t").groupby("event_id").head(1)
        print(
            f"\nbuy all {side.upper()}: {len(hit):,} event-minutes with a locked-in gain "
            f"after fees, "
            f"in {hit.event_id.nunique()} events"
        )
        if len(first):
            print(first.groupby("split")[f"ret_{side}"].describe().round(4).to_string())
            print(
                "minutes the gap lasted per event:",
                hit.groupby("event_id").size().describe().round(1).to_dict(),
            )


def simulate(ev, g, bid, ask, gain_yes, gain_no, a):
    """Seen at t, traded at t + latency with limit orders at the seen prices: a leg fills only if
    its book at t + latency is at least as good. Partial sets are held to resolution. One trade
    per event and side: the first opportunity."""
    y = g.drop_duplicates("mkey").set_index("mkey").y
    split = g.split.iloc[0]
    out = []
    for side, gain in (("yes", gain_yes), ("no", gain_no)):
        hit = gain[gain > a.min_gain]
        if hit.empty:
            continue
        t0 = hit.index[0]
        t1 = t0 + pd.Timedelta(minutes=a.latency)
        if t1 not in ask.index:
            out.append(
                {
                    "event_id": ev,
                    "split": split,
                    "side": side,
                    "t": t0,
                    "legs_filled": 0,
                    "cost": 0.0,
                    "payout": 0.0,
                    "seen_gain": hit.iloc[0],
                }
            )
            continue
        if side == "yes":
            limit, now = ask.loc[t0], ask.loc[t1]
            filled = now <= limit
            px = now[filled]
            pay = y[px.index].sum()
        else:
            limit, now = 1 - bid.loc[t0], 1 - bid.loc[t1]
            filled = now <= limit
            px = now[filled]
            pay = (1 - y[px.index]).sum()
        cost = float((px + C.poly_fee(px)).sum())
        out.append(
            {
                "event_id": ev,
                "split": split,
                "side": side,
                "t": t0,
                "legs_filled": int(filled.sum()),
                "cost": cost,
                "payout": float(pay),
                "seen_gain": hit.iloc[0],
            }
        )
    return pd.DataFrame(out)


def report(d, a):
    d = d[d.legs_filled > 0].assign(
        ret=lambda x: (x.payout - x.cost) / x.cost, cluster=lambda x: x.event_id
    )
    print(f"latency {a.latency} min, min gain {a.min_gain}: one trade per event and side")
    d = d[d.split == ("holdout" if a.holdout else "explore")]
    for (split, side), g in d.groupby(["split", "side"]):
        full = g[g.legs_filled == a.legs]
        print(
            f"{split:8s} {side:3s}: trades {len(g):4d}, all legs filled {len(full) / len(g):.2f}, "
            f"pnl per $1 {C.fmt(C.summarise(g))}; $ pnl {(g.payout - g.cost).sum():+.2f} on "
            f"${g.cost.sum():.0f}"
        )


if __name__ == "__main__":
    main()
