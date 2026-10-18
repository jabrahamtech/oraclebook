"""Idea P5 (PROTOCOL_V6.md): fair value of Kalshi's daily BTC / ETH "above K" ladders from the
options market's volatility instead of realised volatility. fair = Phi(ln(S/K) / (sigma sqrt(tau)))
with sigma = Deribit DVOL / 100 (30-day implied, annual units; the last hourly value known at t),
tau the time to the close in years and S the last Binance close known at t, shifted by round 1's
median Kalshi-over-spot basis. Rule P5(e): buy the side whose fair value beats the ask + fee by e
(asks <= 60 s old), one contract, dollar-weighted return with events resampled. Split as G1
(exploration closes before 2026, holdout 2026).

Usage: python -I cx_iv.py fetch [--start 2024-10-01] [--end 2026-10-15]   # DVOL, public API
       python -I cx_iv.py explore
       python -I cx_iv.py holdout --edge E --alpha 0.01   (frozen rule, run once)
"""

import argparse
import json
import pathlib
import subprocess
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import cx
import cx_models as M
import kx

DERIBIT = "https://www.deribit.com/api/v2/public/get_volatility_index_data"
OUT = cx.RAW / "deribit_dvol"
BASIS = 1e-4  # round 1: Kalshi's strike sits ~1 bp above spot at the open (median)
EDGES = (0.0, 0.03, 0.05)
MAX_AGE = 60
MINUTES_PER_YEAR = 365 * 24 * 60


def get(url, tries=8):
    for i in range(tries):
        r = subprocess.run(["curl", "-s", "-m", "60", url], capture_output=True, text=True,
                           check=False)
        try:
            return json.loads(r.stdout)["result"]
        except (json.JSONDecodeError, KeyError):
            time.sleep(min(30, 2**i))
    sys.exit(f"GET {url} failed after {tries} tries")


def fetch(start, end):
    """Hourly DVOL candles per currency, newest page first; `continuation` pages back in time."""
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    for cur in cx.ASSETS:
        rows, t1 = [], int(pd.Timestamp(end, tz="UTC").timestamp() * 1000)
        while t1 > t0:
            r = get(f"{DERIBIT}?currency={cur}&start_timestamp={t0}&end_timestamp={t1}"
                    "&resolution=3600")
            rows += r.get("data") or []
            nxt = r.get("continuation")
            if not r.get("data") or not nxt or nxt >= t1:
                break
            t1 = nxt
        d = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close"]).drop_duplicates("t")
        d = d.assign(ts=pd.to_datetime(d.t, unit="ms", utc=True)).sort_values("ts")
        d[["ts", "close"]].rename(columns={"close": "dvol"}).to_parquet(
            OUT / f"{cur}.parquet", index=False
        )
        print(f"{cur}: {len(d):,} hourly DVOL values {d.ts.min()} → {d.ts.max()}")


def data():
    d = M.data()
    d["t"] = d.close - pd.to_timedelta(d.left, unit="min")
    parts = []
    for asset, g in d.groupby("asset"):
        v = pd.read_parquet(OUT / f"{asset}.parquet")
        v = v.assign(known=v.ts + pd.Timedelta(hours=1)).sort_values("known")
        g = pd.merge_asof(g.sort_values("t"), v[["known", "dvol"]], left_on="t", right_on="known",
                          direction="backward", tolerance=pd.Timedelta(hours=3))
        parts.append(g)
    d = pd.concat(parts, ignore_index=True)
    sigma = d.dvol / 100 * np.sqrt(d.left / MINUTES_PER_YEAR)
    z_iv = np.log(d.spot * (1 + BASIS) / d.strike) / sigma
    d["p_iv"] = M.PHI(z_iv).astype(float)
    d["p_rv"] = M.PHI(d.z).astype(float)
    d["p_blend"] = 0.5 * d.p_iv + 0.5 * d["last"]
    return d


def explore():
    d = data()
    e = d[d.close < M.SPLIT]
    print(f"exploration: {len(e):,} rows, {e.event.nunique()} events; "
          f"DVOL missing on {e.dvol.isna().mean():.1%} of rows")
    for col in ("p_iv", "p_rv", "p_blend"):
        m_, k_, n_ = M.brier(e, col)
        print(f"{col}: Brier model {m_:.4f} vs market {k_:.4f} on {n_:,} rows")
    print("\nrule grid (exploration, buy where the DVOL fair value beats ask + fee by e, "
          f"asks <= {MAX_AGE} s old)")
    for edge in EDGES:
        f = M.takers(e, e.p_iv, edge, MAX_AGE)
        print(f"  edge {edge:.2f}: {kx.fmt(kx.ratio_boot(f, 'event'))}")


def holdout(edge, alpha):
    d = data()
    h = d[d.close >= M.SPLIT]
    f = M.takers(h, h.p_iv, edge, MAX_AGE)
    print(f"HOLDOUT P5 edge {edge} alpha {alpha}: {h.event.nunique()} events "
          f"{h.close.min():%Y-%m-%d} → {h.close.max():%Y-%m-%d}: "
          f"{kx.fmt(kx.ratio_boot(f, 'event', alpha=alpha))}")


def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("mode", choices=["fetch", "explore", "holdout"])
    ap.add_argument("--start", default="2024-10-01")
    ap.add_argument("--end", default="2026-10-15")
    ap.add_argument("--edge", type=float, default=0.0)
    ap.add_argument("--alpha", type=float, default=0.05)
    a = ap.parse_args()
    if a.mode == "fetch":
        fetch(a.start, a.end)
    elif a.mode == "explore":
        explore()
    else:
        holdout(a.edge, a.alpha)


if __name__ == "__main__":
    main()
