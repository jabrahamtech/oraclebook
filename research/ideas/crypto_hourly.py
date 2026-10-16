"""Round 2, idea H: Kalshi hourly "BTC/ETH above K at hour end" markets vs a random-walk fair
value from Binance. P(above) = Phi((ln(S_t * e^basis / K)) / (sigma * sqrt(tau))), S_t the latest
Binance 1-minute close known at t, sigma the trailing 60-minute realised vol, basis = median of
ln(settlement value / Binance close at expiry) on exploration (Kalshi settles on the CF
Benchmarks index, averaged over the last minute). At t = close - h the executable price of a
side is its most recent taker-buy print in the previous 5 minutes (a taker buy prints at the
ask). Buy the side whose model probability beats that price plus the taker fee by > theta.

Usage: python -I crypto_hourly.py explore
       python -I crypto_hourly.py holdout --h MIN --theta X
"""

import argparse
import math
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common as C
import kx

SERIES = {"KXBTCD": "btc", "KXETHD": "eth"}
HS = (50, 30, 15, 5)


def phi(x):
    return 0.5 * (1 + np.vectorize(math.erf)(np.asarray(x, float) / math.sqrt(2)))


def load():
    m, t = kx.load([s for s in SERIES if s in kx.available()])
    m = m[m.strike_type.isin(["greater", "greater_or_equal"]) & m.ticker.isin(t.ticker)].copy()
    m["K"] = m.floor_strike
    m["settle"] = pd.to_numeric(m.expiration_value, errors="coerce")
    rows = []
    for s, coin in SERIES.items():
        ms = m[m.series == s]
        if ms.empty:
            continue
        k = C.with_vol(C.klines(coin))
        end = C.spot_at(k, ms.close_time)
        ms = ms.assign(spot_end=end.close.to_numpy())
        basis = np.log(ms.settle / ms.spot_end)[ms.split == "explore"].median()
        for h in HS:
            tt = ms.close_time - pd.Timedelta(minutes=h)
            sp = C.spot_at(k, tt)
            rows.append(
                ms.assign(
                    h=h,
                    t=tt.to_numpy(),
                    S=sp.close.to_numpy(),
                    sigma=sp.sigma.to_numpy(),
                    basis=basis,
                )
            )
    d = pd.concat(rows, ignore_index=True)
    d["tau"] = d.h - 0.5
    d["p_model"] = phi(np.log(d.S * np.exp(d.basis) / d.K) / (d.sigma * np.sqrt(d.tau)))
    # Executable prices: last taker buy of each side in the 5 minutes before t.
    tt = t[["ticker", "ts", "yes_price", "taker_side"]]
    px = []
    for h in HS:
        dh = d[d.h == h][["ticker", "t"]]
        w = tt.merge(dh, on="ticker")
        w = w[(w.ts <= w.t) & (w.ts > w.t - pd.Timedelta(minutes=5))]
        last = w.sort_values("ts").groupby(["ticker", "taker_side"]).yes_price.last().unstack()
        last = last.reindex(columns=["yes", "no"]).rename(
            columns={"yes": "ask_yes", "no": "yes_of_no"}
        )
        px.append(last.reset_index().assign(h=h))
    d = d.merge(pd.concat(px), on=["ticker", "h"], how="left")
    d["ask_no"] = 1 - d.yes_of_no
    d["cluster"] = d.close_time.dt.strftime("%Y-%m-%d")
    return d


def trades_for(d, theta):
    rows = []
    for side, ask, pw, yv in (
        ("yes", d.ask_yes, d.p_model, d.y),
        ("no", d.ask_no, 1 - d.p_model, 1 - d.y),
    ):
        fee = d.fee_taker * ask * (1 - ask)
        edge = pw - ask - fee
        ok = ask.between(0.01, 0.99) & (edge > theta)
        rows.append(d[ok].assign(side=side, price=ask[ok], fee=fee[ok], yy=yv[ok], edge=edge[ok]))
    t = pd.concat(rows).sort_values("edge").groupby(["ticker", "h"]).tail(1)
    return t.assign(count=1.0, cost=t.price + t.fee, pnl=t.yy - t.price - t.fee)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["explore", "holdout"])
    ap.add_argument("--h", type=int)
    ap.add_argument("--theta", type=float)
    ap.add_argument("--alpha", type=float, default=0.05)
    a = ap.parse_args()
    d = load()
    print(
        f"markets {d.ticker.nunique():,}; basis (bp) "
        f"{d.groupby('series').basis.first().mul(1e4).round(2).to_dict()}"
    )
    if a.mode == "holdout":
        tr = trades_for(d[(d.split == "holdout") & (d.h == a.h)], a.theta)
        print(
            f"HOLDOUT hourly h={a.h} theta={a.theta}: "
            f"{kx.fmt(kx.ratio_boot(tr, 'cluster', alpha=a.alpha))}"
        )
        return
    e = d[d.split == "explore"]
    both = e.dropna(subset=["ask_yes", "ask_no", "p_model"])
    for h, g in both.groupby("h"):
        mid = (g.ask_yes + 1 - g.ask_no) / 2
        print(
            f"h={h:2d} min: n={len(g):5d} brier mid {((mid - g.y) ** 2).mean():.4f} "
            f"model {((g.p_model - g.y) ** 2).mean():.4f}"
        )
    for h in HS:
        for theta in (0.0, 0.03, 0.05, 0.10):
            tr = trades_for(e[e.h == h], theta)
            print(f"h={h:2d} theta={theta:.2f}: {kx.fmt(kx.ratio_boot(tr, 'cluster'))}")


if __name__ == "__main__":
    main()
