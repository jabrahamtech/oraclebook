"""Idea 3: be the maker instead of the taker. At t0 = scheduled close - h, rest a buy order on a
token at the best bid (or one tick better when the spread allows). It counts as filled only if
that token's best ask later drops *below* the order price before the close: a seller then crossed
our level, so we were filled whatever our queue position (conservative; same rule as the Replay
paper executor). Filled contracts are held to resolution; makers pay no fee. Fills happen when the
price moves against us, so adverse selection is included.

Datasets with per-second books: kachoio (outcomes rebuilt from Binance where the publisher left
them null; see crypto_favourites.py) and polyorderbooks.

Usage: python -I maker.py <kachoio|polyorderbooks> explore
       python -I maker.py <name> holdout --horizon H --side S --lo X --hi Y --improve I
"""

import argparse
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common as C
import crypto_favourites as CF

TD = pd.Timedelta
TICK = 0.01


def orders_and_fills(m, q, horizons):
    """For each market, token and horizon: the book at t0 and the lowest ask after t0."""
    snap = C.P.sample(m, q, "close_ts", {f"{h}s": -TD(seconds=h) for h in horizons}, TD("5s"))
    q = q.merge(m[["mkey", "close_ts"]], on="mkey")
    rel = (q.close_ts - q.ts).dt.total_seconds()
    out = []
    for h in horizons:
        w = q[(rel > 0) & (rel < h)]
        lo = w.groupby(["mkey", "token"]).ask.min().rename("min_ask_after").reset_index()
        out.append(lo.assign(horizon=f"{h}s"))
    lo = pd.concat(out, ignore_index=True)
    return snap.merge(lo, on=["mkey", "token", "horizon"], how="left")


def load(name):
    if name == "kachoio":
        mk, _snap, cut = CF.load()
        mk = mk.assign(y=mk.y.fillna(mk.y_spot))
        parts = {c: mk[mk.coin == c] for c in sorted(mk.coin.unique())}
        horizons = (240, 120, 60, 30)
    else:
        mk, cut = C.markets(name)
        parts = {length: mk[mk.length == length] for length in ("5m", "15m", "4h")}
        horizons = None
    frames = []
    for part, m in parts.items():
        hz = (
            horizons
            or {"5m": (240, 120, 60), "15m": (840, 600, 300, 60), "4h": (10800, 3600, 1800, 300)}[
                part
            ]
        )
        q = next(C.quotes(name, parts=[part]))
        q = q[q.bid.notna() & q.ask.notna() & (q.bid < q.ask)]
        frames.append(orders_and_fills(m.drop(columns=[c for c in ("y_spot",) if c in m]), q, hz))
    s = pd.concat(frames, ignore_index=True)
    s["y_token"] = np.where(s.token == 0, s.y, 1 - s.y)
    s["cluster"] = s.close_ts.dt.strftime("%Y-%m-%d")
    return s[s.y.notna() & (s.bid > 0) & (s.ask < 1)], cut


def fills(s, lo, hi, improve):
    mid = (s.bid + s.ask) / 2
    px = (s.bid + improve * TICK).round(4)
    px = px.where(px < s.ask)  # never cross the spread
    sel = s[(mid >= lo) & (mid < hi) & px.notna()].assign(price=px)
    sel = sel.assign(filled=sel.min_ask_after < sel.price)
    f = sel[sel.filled].assign(y=lambda d: d.y_token)
    f["ret"] = (f.y - f.price) / f.price
    return sel, f


def explore(s):
    e = s[s.split == "explore"]
    rows = []
    for h in sorted(e.horizon.unique(), key=lambda x: -int(x[:-1])):
        eh = e[e.horizon == h]
        for lo, hi in ((0.05, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 0.9), (0.9, 0.97)):
            for improve in (0, 1):
                sel, f = fills(eh, lo, hi, improve)
                taker = sel.assign(
                    ret=(sel.y_token - sel.ask - C.poly_fee(sel.ask))
                    / (sel.ask + C.poly_fee(sel.ask))
                )
                rows.append(
                    {
                        "horizon": h,
                        "mid_band": f"[{lo},{hi})",
                        "improve": improve,
                        "orders": len(sel),
                        "fill_rate": len(f) / max(len(sel), 1),
                        **{
                            f"maker_{k}": v
                            for k, v in C.summarise(f).items()
                            if k in ("n", "ret", "lo", "hi")
                        },
                        "taker_ret": C.summarise(taker).get("ret"),
                    }
                )
    r = pd.DataFrame(rows)
    print("exploration: resting bid at best bid (+improve ticks), filled only on trade-through")
    print(r.to_string(index=False, float_format=lambda v: f"{v:.4f}"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("mode", choices=["explore", "holdout"])
    ap.add_argument("--horizon")
    ap.add_argument("--lo", type=float)
    ap.add_argument("--hi", type=float)
    ap.add_argument("--improve", type=int, default=0)
    ap.add_argument("--alpha", type=float, default=0.05)
    a = ap.parse_args()
    s, cut = load(a.name)
    print(f"{a.name}: split at close {cut}")
    if a.mode == "explore":
        explore(s)
        return
    h = s[(s.split == "holdout") & (s.horizon == a.horizon)]
    sel, f = fills(h, a.lo, a.hi, a.improve)
    print(
        f"HOLDOUT {a.name} {a.horizon} mid in [{a.lo},{a.hi}) improve={a.improve}: orders "
        f"{len(sel)}, fill rate {len(f) / max(len(sel), 1):.3f}, "
        f"{C.fmt(C.summarise(f, alpha=a.alpha))}"
    )


if __name__ == "__main__":
    main()
