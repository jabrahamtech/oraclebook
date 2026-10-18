"""Idea B2 (PROTOCOL_V6.md, Amendments 1-2): do monthly "What price will <coin> hit?" markets
overprice upside longshots, especially in bull runs? One day after the month starts, for every
upside strike at least k above the Binance close known then, with the last YES price in the
previous 24 h <= 0.20: buy NO at 1 - YES + 1c spread allowance + the 0.07 fee; hold to resolution.

Data: polymarket_api.py <raw> events --name hit --title "^What price will (Bitcoin|Ethereum|Solana)
      hit in" --start 2024-11-01 --end 2026-10-15, then
      polymarket_api.py <raw> prices --series events-hit --token yes_token --from-col month_start
      --minutes 1440 --fidelity 60; bull.py fetch

Usage: python -I pm_hit_longshots.py explore
       python -I pm_hit_longshots.py holdout --k K --regime bull|all --alpha 0.0071   (once)
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
SYMBOL = {"Bitcoin": "BTCUSDT", "Ethereum": "ETHUSDT", "Solana": "SOLUSDT"}
KS = (0.15, 0.30)
MAX_YES = 0.20
SPREAD = 0.01


def load():
    folder = C.RAW / "polymarket_api" / "events-hit"
    m = pd.read_parquet(folder / "markets.parquet")
    p = pd.read_parquet(folder / "prices.parquet")
    p = p.assign(ts=pd.to_datetime(p.t, unit="s", utc=True).astype(C.UTC)).sort_values("ts")
    m = m[m.upside].assign(t=(m.month_start + TD("1D")).astype(C.UTC)).sort_values("t")
    x = pd.merge_asof(m, p[["token", "ts", "p"]], left_on="t", right_on="ts",
                      left_by="yes_token", right_by="token", direction="backward",
                      tolerance=TD("24h")).dropna(subset=["p"])
    x["symbol"] = x.title.str.extract(f"({'|'.join(SYMBOL)})", expand=False).map(SYMBOL)
    x = x.dropna(subset=["symbol"])
    spot = pd.concat([bull.ret30(g.t, s).close.set_axis(g.index) for s, g in x.groupby("symbol")])
    x["dist"] = x.strike / spot.reindex(x.index) - 1
    x["bull"] = bull.is_bull(x.t)
    cut = x.close_ts.quantile(C.EXPLORE_SHARE)
    price = 1 - x.p + SPREAD
    x, price = x[price < 1], price[price < 1]  # no entry when the NO would cost $1 or more
    fee = C.poly_fee(price)
    return x.assign(
        split=np.where(x.close_ts < cut, "explore", "holdout"), price=price, y=1 - x.y,
        ret=(1 - x.y - price - fee) / (price + fee), cluster=x.event_id,
    ), cut


def rule(d, k, regime):
    x = d[(d.p <= MAX_YES) & (d.dist >= k)]
    return x[x.bull] if regime == "bull" else x


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["explore", "holdout"])
    ap.add_argument("--k", type=float)
    ap.add_argument("--regime", choices=["bull", "all"])
    ap.add_argument("--alpha", type=float, default=0.05)
    a = ap.parse_args()
    d, cut = load()
    print(f"B2: {len(d):,} upside strikes, {d.event_id.nunique()} events, split {cut:%Y-%m-%d}")
    if a.mode == "explore":
        e = d[d.split == "explore"]
        for k in KS:
            for regime in ("bull", "all"):
                print(f"  k={k:.2f} {regime}: {C.fmt(C.summarise(rule(e, k, regime)))}")
        return
    h = rule(d[d.split == "holdout"], a.k, a.regime)
    print(f"HOLDOUT B2 k={a.k} {a.regime} alpha={a.alpha}: "
          f"{C.fmt(C.summarise(h, alpha=a.alpha))}")


if __name__ == "__main__":
    main()
