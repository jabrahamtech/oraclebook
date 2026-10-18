"""Idea B1 (PROTOCOL_V6.md, Amendments 1-2): in a bull regime, does Up win more often than its
price in hourly / 4-hour crypto Up/Down? At the open + 2 min (1h) or + 5 min (4h), buy Up at the
last prices-history point (<= 2 min old) + 1c spread allowance + the 0.07 fee; hold. Bull rows are
the test; non-bull rows are a control.

Data: polymarket_api.py <raw> updown --interval 1h|4h (BTC, ETH, 2024-11-01 -> 2026-10-14), then
      polymarket_api.py <raw> prices --series btc-1h,eth-1h,... --minutes 10 --fidelity 1
      bull.py fetch

Usage: python -I pm_bull_drift.py explore
       python -I pm_bull_drift.py holdout --interval 1h|4h --alpha 0.0071   (frozen, run once)
"""

import argparse
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bull
import common as C

TD = pd.Timedelta
DECIDE = {"1h": TD("2min"), "4h": TD("5min")}
SPREAD = 0.01
MAX_AGE = TD("2min")


def load():
    rows = []
    for interval, wait in DECIDE.items():
        for coin in ("btc", "eth"):
            folder = C.RAW / "polymarket_api" / f"{coin}-{interval}"
            if not (folder / "prices.parquet").exists():
                continue
            m = pd.read_parquet(folder / "markets.parquet")
            p = pd.read_parquet(folder / "prices.parquet")
            p = p.assign(ts=pd.to_datetime(p.t, unit="s", utc=True).astype(C.UTC)).sort_values("ts")
            m = m.assign(t=(m.open_ts + wait).astype(C.UTC)).sort_values("t")
            x = pd.merge_asof(m, p[["token", "ts", "p"]], left_on="t", right_on="ts",
                              left_by="up_token", right_by="token", direction="backward",
                              tolerance=MAX_AGE)
            rows.append(x.dropna(subset=["p"]).assign(interval=interval, coin=coin))
    if not rows:
        raise FileNotFoundError("no hourly/4h prices under research/data/raw/polymarket_api")
    d = pd.concat(rows, ignore_index=True)
    d["bull"] = bull.is_bull(d.t)
    cut = d.close_ts.quantile(C.EXPLORE_SHARE)
    price = d.p + SPREAD
    d, price = d[price < 1], price[price < 1]
    fee = C.poly_fee(price)
    return d.assign(
        split=np.where(d.close_ts < cut, "explore", "holdout"), price=price,
        ret=(d.y - price - fee) / (price + fee), cluster=d.close_ts.dt.strftime("%Y-%m-%d"),
    ), cut


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["explore", "holdout"])
    ap.add_argument("--interval", choices=list(DECIDE))
    ap.add_argument("--alpha", type=float, default=0.05)
    a = ap.parse_args()
    d, cut = load()
    print(f"B1: {len(d):,} markets, bull share {d.bull.mean():.0%}, split {cut:%Y-%m-%d}")
    if a.mode == "explore":
        e = d[d.split == "explore"]
        for (iv, b), g in e.groupby(["interval", "bull"]):
            print(f"  {iv} {'bull' if b else 'control'}: Up rate {g.y.mean():.3f} vs price "
                  f"{g.p.mean():.3f}; {C.fmt(C.summarise(g))}")
        return
    h = d[(d.split == "holdout") & d.bull & (d.interval == a.interval)]
    print(f"HOLDOUT B1 {a.interval} alpha={a.alpha}: {C.fmt(C.summarise(h, alpha=a.alpha))}")


if __name__ == "__main__":
    main()
