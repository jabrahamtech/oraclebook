"""Idea B3 (PROTOCOL_V6.md, Amendment 1): Hyperliquid funding carry. At each UTC day start, if a
perp's trailing 7-day mean funding annualises to >= F, short the perp and hold spot for 7 days.
Return per $1 of spot = funding received + (premium at entry - premium at exit) - 0.20% (four
taker legs). A yield, not a prediction: also printed as APR next to a 5% cash yield.

Usage: python -I hl_funding.py fetch [--start 2024-01-01] [--end 2026-10-15]
       python -I hl_funding.py explore
       python -I hl_funding.py holdout --apr F --alpha 0.0071   (frozen rule, run once)
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
import common as C

INFO = "https://api.hyperliquid.xyz/info"
COINS = ("BTC", "ETH", "SOL")
APRS = (0.10, 0.20, 0.30)
HOLD = pd.Timedelta("7D")
COST = 0.002
HOURS_PER_YEAR = 24 * 365


def folder():
    return C.RAW / "hyperliquid_funding"


def post(body, tries=12):
    """JSON body of a 200 reply; anything else is retried (a 429's body is `null`, which would
    otherwise read as the end of the history)."""
    for i in range(tries):
        r = subprocess.run(
            ["curl", "-s", "-m", "60", "-w", "\n%{http_code}", "-H",
             "Content-Type: application/json", "-d", json.dumps(body), INFO],
            capture_output=True, text=True, check=False,
        )
        text, _, code = r.stdout.rpartition("\n")
        if code == "200":
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass
        time.sleep(min(30, 2**i))
    sys.exit(f"POST {body} failed after {tries} tries")


def fetch(start, end):
    folder().mkdir(parents=True, exist_ok=True)
    t1 = int(pd.Timestamp(end, tz="UTC").timestamp() * 1000)
    for coin in COINS:
        rows, t = [], int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
        while t < t1:
            page = post({"type": "fundingHistory", "coin": coin, "startTime": t, "endTime": t1})
            if not page:
                break
            rows += [(r["time"], float(r["fundingRate"]), float(r["premium"])) for r in page]
            t = page[-1]["time"] + 1
        d = pd.DataFrame(rows, columns=["time", "rate", "premium"]).drop_duplicates("time")
        d["ts"] = pd.to_datetime(d.time, unit="ms", utc=True)
        d.drop(columns="time").to_parquet(folder() / f"{coin}.parquet", index=False)
        print(f"{coin}: {len(d):,} funding prints {d.ts.min():%Y-%m-%d} → {d.ts.max():%Y-%m-%d}")


def holds():
    """One row per (coin, UTC day): trailing 7-day funding APR and the 7-day carry return."""
    out = []
    for coin in COINS:
        f = pd.read_parquet(folder() / f"{coin}.parquet").set_index("ts").sort_index()
        days = pd.date_range(f.index.min().ceil("D") + HOLD, f.index.max().floor("D") - HOLD,
                             freq="D")
        cum = f.rate.cumsum()

        def at(t, cum=cum):
            return cum.asof(t)  # cumulative funding up to and including t

        trail = np.array([(at(d) - at(d - HOLD)) for d in days]) / (HOLD / pd.Timedelta("1h"))
        carry = np.array([at(d + HOLD) - at(d) for d in days])
        prem = np.array([f.premium.asof(d) - f.premium.asof(d + HOLD) for d in days])
        out.append(pd.DataFrame({
            "coin": coin, "day": days, "apr": trail * HOURS_PER_YEAR,
            "ret": carry + prem - COST, "cluster": days.strftime("%G-W%V"),
        }))
    d = pd.concat(out, ignore_index=True)
    cut = d.day.quantile(C.EXPLORE_SHARE)
    return d.assign(split=np.where(d.day < cut, "explore", "holdout")), cut


def line(x, alpha=0.05):
    s = C.summarise(x, alpha=alpha)
    apr = f" ≈ {s['ret'] * 52:+.1%} APR" if s.get("n") else ""
    return C.fmt(s) + apr


def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("mode", choices=["fetch", "explore", "holdout"])
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2026-10-15")
    ap.add_argument("--apr", type=float)
    ap.add_argument("--alpha", type=float, default=0.05)
    a = ap.parse_args()
    if a.mode == "fetch":
        fetch(a.start, a.end)
        return
    d, cut = holds()
    print(f"B3: {len(d):,} coin-days, split {cut:%Y-%m-%d}; per 7-day hold, cash ≈ +0.10%")
    if a.mode == "explore":
        e = d[d.split == "explore"]
        print(f"  always on: {line(e)}")
        for f in APRS:
            print(f"  trailing APR >= {f:.0%}: {line(e[e.apr >= f])}")
        return
    h = d[(d.split == "holdout") & (d.apr >= a.apr)]
    print(f"HOLDOUT B3 apr>={a.apr} alpha={a.alpha}: {line(h, a.alpha)}")


if __name__ == "__main__":
    main()
