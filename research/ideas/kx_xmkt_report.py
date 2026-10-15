"""Round 3: summarise kx_live_xmkt.py recordings.

1. Riskless crossings, grouped into episodes (same hour, kind and ladder market): how long they
   lasted (polls ~5 s apart), best net edge after taker fees, displayed depth.
2. Relative value: the 15-minute mid against the hourly ladder interpolated at K* (linear between
   the nearest strikes either side), scored against the 15-minute outcome (fetched from the
   public API once settled).

Usage: python -I kx_xmkt_report.py <edges.csv>
"""

import argparse
import json
import pathlib
import subprocess

import numpy as np
import pandas as pd

BASE = "https://api.elections.kalshi.com/trade-api/v2"


def result(ticker):
    r = subprocess.run(
        ["curl", "-s", "-m", "20", f"{BASE}/markets/{ticker}"],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        m = json.loads(r.stdout).get("market", {})
    except json.JSONDecodeError:
        return np.nan
    return {"yes": 1.0, "no": 0.0}.get(m.get("result"), np.nan)


def episodes(e):
    if e.empty:
        print("no crossings recorded")
        return
    e = e.assign(ts=pd.to_datetime(e.ts), hour=e.ticker.str.extract(r"-(\d{2}[A-Z]{3}\d{4})")[0])
    e = e.sort_values("ts")
    gap = e.groupby(["asset", "kind", "ticker"]).ts.diff().dt.total_seconds().fillna(1e9)
    e["episode"] = (gap > 20).cumsum()
    g = e.groupby("episode").agg(
        asset=("asset", "first"),
        kind=("kind", "first"),
        ticker=("ticker", "first"),
        start=("ts", "min"),
        polls=("ts", "size"),
        best_net=("net", "max"),
        depth=("depth", "max"),
    )
    g["secs"] = g.polls * 5
    print(
        f"crossing episodes: {len(g)}; with net > 0: {(g.best_net > 0).sum()}; "
        f"net > 0 and depth >= 10: {((g.best_net > 0) & (g.depth >= 10)).sum()}"
    )
    print(g.sort_values("best_net", ascending=False).head(15).to_string())


def relative(b):
    b = b.assign(ts=pd.to_datetime(b.ts))
    rows = []
    for (ts, asset), g in b.groupby(["ts", "asset"]):
        f = g[g.ticker.str.contains("15M")]
        if f.empty:
            continue
        f = f.iloc[0]
        lad = g[~g.ticker.str.contains("15M")].dropna(subset=["bid", "ask"])
        lo, hi = lad[lad.strike < f.strike].tail(1), lad[lad.strike >= f.strike].head(1)
        if lo.empty or hi.empty or pd.isna(f.bid) or pd.isna(f.ask):
            continue
        m1, m2 = (lo.bid.iloc[0] + lo.ask.iloc[0]) / 2, (hi.bid.iloc[0] + hi.ask.iloc[0]) / 2
        k1, k2 = lo.strike.iloc[0], hi.strike.iloc[0]
        p_hat = m1 + (f.strike - k1) / (k2 - k1) * (m2 - m1)
        rows.append((ts, asset, f.ticker, (f.bid + f.ask) / 2, p_hat, f.ask - f.bid))
    r = pd.DataFrame(rows, columns=["ts", "asset", "ticker", "mid15", "ladder", "spread15"])
    if r.empty:
        print("no comparable polls")
        return
    res = {t: result(t) for t in r.ticker.unique()}
    r["y"] = r.ticker.map(res)
    close = pd.to_datetime(r.ticker.str.extract(r"-(\d{2}[A-Z]{3}\d{6})")[0], format="%y%b%d%H%M")
    close = close.dt.tz_localize("America/New_York").dt.tz_convert("UTC")
    r["left"] = (close - r.ts).dt.total_seconds() / 60
    s = r.dropna(subset=["y"])
    print(f"polls {len(r)} over {r.ticker.nunique()} windows; settled {s.ticker.nunique()}")
    print(
        f"mean |15m mid - ladder| {np.abs(r.mid15 - r.ladder).mean():.3f}, "
        f"mean 15m spread {r.spread15.mean():.3f}"
    )
    if len(s):
        for name, col in (("15m mid", "mid15"), ("ladder", "ladder")):
            print(f"  Brier {name}: {((s[col] - s.y) ** 2).mean():.4f}")
        s = s.assign(bucket=pd.cut(s.left, [0, 2, 5, 10, 15]))
        print(
            s.groupby("bucket", observed=True)
            .apply(
                lambda d: pd.Series(
                    {
                        "n": len(d),
                        "brier15": ((d.mid15 - d.y) ** 2).mean(),
                        "brier_ladder": ((d.ladder - d.y) ** 2).mean(),
                    }
                )
            )
            .round(4)
            .to_string()
        )


def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("edges")
    a = ap.parse_args()
    p = pathlib.Path(a.edges)
    episodes(pd.read_csv(p))
    relative(pd.read_csv(p.with_name(p.stem + "_books.csv")))


if __name__ == "__main__":
    main()
