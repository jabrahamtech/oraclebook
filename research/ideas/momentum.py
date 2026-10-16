"""Idea 4: under- or over-reaction. Does the recent change in a market's price predict the outcome
beyond the current price? Features at t = scheduled close - h: logit(mid_t) and the change
logit(mid_t) - logit(mid_{t-L}). Out-of-sample check: fit on the first half of exploration (by
close), score log loss on the second half. Then a rule grid: when the move exceeds a threshold,
buy (taker, at the ask, with fees) the side that moved up (momentum) or the side that moved down
(reversal).

Usage: python -I momentum.py <worldcup|kalshi|kachoio> explore
       python -I momentum.py <name> holdout --horizon H --lookback L --kind K --move X
"""

import argparse
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common as C

TD = pd.Timedelta
SETUP = {
    "worldcup": {
        "h": ["1D", "6h", "1h"],
        "L": ["6h", "1D"],
        "stale": "6h",
        "before": "resolved_ts",
        "fee": "pm",
        "cluster": "event_id",
        "tokens": (0,),
    },
    "kalshi": {
        "h": ["10min", "5min", "2min"],
        "L": ["2min", "4min"],
        "stale": "59s",
        "before": None,
        "fee": "kx",
        "cluster": "day",
        "tokens": (0,),
    },
    "kachoio": {
        "h": ["120s", "60s"],
        "L": ["30s", "60s"],
        "stale": "5s",
        "before": None,
        "fee": "pm",
        "cluster": "day",
        "tokens": (0, 1),
    },
}


def load(name):
    cfg = SETUP[name]
    m, cut = C.markets(name)
    if name == "kachoio":
        import crypto_favourites as CF

        mk, _s, _c = CF.load()
        m = m.merge(mk[["mkey", "y_spot"]], on="mkey")
        m["y"] = m.y.fillna(m.y_spot)
    offs = {}
    for h in cfg["h"]:
        offs[f"{h}|now"] = -TD(h)
        for lb in cfg["L"]:
            offs[f"{h}|{lb}"] = -TD(h) - TD(lb)
    cols = ["mkey", "token", "ts", "bid", "ask"]
    snap = C.P.sample(
        m,
        C.quotes(name, columns=cols),
        "close_ts",
        offs,
        TD(cfg["stale"]),
        before=cfg["before"],
        tokens=cfg["tokens"],
    )
    snap = snap[(snap.bid > 0) & (snap.ask < 1) & (snap.bid <= snap.ask)]
    snap["mid"] = (snap.bid + snap.ask) / 2
    snap[["h", "lb"]] = snap.horizon.str.split("|", expand=True)
    now = snap[snap.lb == "now"]
    rows = []
    for lb in cfg["L"]:
        past = snap[snap.lb == lb][["mkey", "token", "h", "mid"]].rename(
            columns={"mid": "mid_past"}
        )
        rows.append(now.merge(past, on=["mkey", "token", "h"]).assign(lb=lb))
    d = pd.concat(rows, ignore_index=True)
    d = d[d.token == 0]  # one row per market; the second token is the mirror (or the complement)
    d["move"] = C.logit(d.mid) - C.logit(d.mid_past)
    d["cluster"] = (
        d.event_id if cfg["cluster"] == "event_id" else d.close_ts.dt.strftime("%Y-%m-%d")
    )
    return d[d.y.notna()], cut, cfg


def trades(d, kind, move, fee_kind):
    fee = C.kalshi_fee if fee_kind == "kx" else C.poly_fee
    up = d[d.move > move]
    dn = d[d.move < -move]
    # Momentum buys the side that rose; reversal buys the side that fell. Buying token 0 costs its
    # ask; buying the other side costs 1 - bid.
    if kind == "momentum":
        buy0, buy1 = up, dn
    else:
        buy0, buy1 = dn, up
    a = buy0.assign(price=buy0.ask, y=buy0.y)
    b = buy1.assign(price=1 - buy1.bid, y=1 - buy1.y)
    t = pd.concat([a, b])
    t = t[(t.price > 0) & (t.price < 1) & ((t.ask - t.bid) <= 0.05)]
    f = fee(t.price)
    return t.assign(ret=(t.y - t.price - f) / (t.price + f))


def explore(d, cfg):
    e = d[d.split == "explore"]
    half = e.close_ts.quantile(0.5)
    rows = []
    for (h, lb), g in e.groupby(["h", "lb"]):
        a, b = g[g.close_ts < half], g[g.close_ts >= half]
        if len(a) < 300 or len(b) < 300:
            continue
        b0 = C.logreg(C.logit(a.mid)[:, None], a.y)
        b1 = C.logreg(np.column_stack([C.logit(a.mid), a.move]), a.y)
        p0 = C.predict(b0, C.logit(b.mid)[:, None])
        p1 = C.predict(b1, np.column_stack([C.logit(b.mid), b.move]))
        rows.append(
            {
                "h": h,
                "lb": lb,
                "n": len(g),
                "move_coef": b1[2],
                "gain_vs_m0": C.logloss(p0, b.y) - C.logloss(p1, b.y),
            }
        )
    print("does the recent move add information? (positive move_coef = momentum)")
    print(pd.DataFrame(rows).to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    rows = []
    for (h, lb), g in e.groupby(["h", "lb"]):
        sd = g.move.std()
        for kind in ("momentum", "reversal"):
            for k in (0.5, 1.0, 2.0):
                t = trades(g, kind, k * sd, cfg["fee"])
                rows.append({"h": h, "lb": lb, "kind": kind, "move_sd": k, **C.summarise(t)})
    print("\nrule grid (exploration, taker with fees, spread <= 5c)")
    print(pd.DataFrame(rows).to_string(index=False, float_format=lambda v: f"{v:.4f}"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("mode", choices=["explore", "holdout"])
    ap.add_argument("--horizon")
    ap.add_argument("--lookback")
    ap.add_argument("--kind")
    ap.add_argument("--move", type=float, help="threshold in sd of the exploration move")
    ap.add_argument("--alpha", type=float, default=0.05)
    a = ap.parse_args()
    d, cut, cfg = load(a.name)
    print(f"{a.name}: split at close {cut}")
    if a.mode == "explore":
        explore(d, cfg)
        return
    sd = d[(d.split == "explore") & (d.h == a.horizon) & (d.lb == a.lookback)].move.std()
    h = d[(d.split == "holdout") & (d.h == a.horizon) & (d.lb == a.lookback)]
    t = trades(h, a.kind, a.move * sd, cfg["fee"])
    print(
        f"HOLDOUT {a.name} h={a.horizon} L={a.lookback} {a.kind} move>{a.move}sd: "
        f"{C.fmt(C.summarise(t, alpha=a.alpha))}"
    )


if __name__ == "__main__":
    main()
