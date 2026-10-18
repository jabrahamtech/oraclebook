"""Bull-regime flag for round 6 (PROTOCOL_V6.md, Amendment 1): BTC's 30-day log return from the
last Binance daily close known at the decision time is above ln(1.10). Daily closes come from
Binance Vision (monthly files, plus daily files for the last month).

Usage: python -I bull.py fetch [--start 2024-09] [--end 2026-10-15]
"""

import argparse
import io
import pathlib
import subprocess
import sys
import zipfile

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common as C

VISION = "https://data.binance.vision/data/spot"
THRESHOLD = np.log(1.10)


def folder():
    return C.RAW / "binance_klines_1d"


def _zip_csv(url):
    r = subprocess.run(["curl", "-sf", "-m", "60", url], capture_output=True, check=False)
    if r.returncode:
        return None
    with zipfile.ZipFile(io.BytesIO(r.stdout)) as z:
        return pd.read_csv(z.open(z.namelist()[0]), header=None, usecols=[0, 4],
                           names=["t", "close"])


def fetch(start, end, symbols=("BTCUSDT", "ETHUSDT", "SOLUSDT")):
    folder().mkdir(parents=True, exist_ok=True)
    last = pd.Timestamp(end, tz="UTC")
    months = pd.period_range(start, (last - pd.offsets.MonthBegin(1)).strftime("%Y-%m"), freq="M")
    days = pd.date_range(last.replace(day=1), last - pd.Timedelta(days=1), freq="D")
    for sym in symbols:
        urls = [f"{VISION}/monthly/klines/{sym}/1d/{sym}-1d-{m}.zip" for m in months]
        urls += [f"{VISION}/daily/klines/{sym}/1d/{sym}-1d-{d:%Y-%m-%d}.zip" for d in days]
        parts = [p for p in map(_zip_csv, urls) if p is not None]
        k = pd.concat(parts, ignore_index=True)
        k = k[pd.to_numeric(k.t, errors="coerce").notna()].astype({"t": "int64", "close": float})
        unit = np.where(k.t > 10**14, "us", "ms")
        k["day"] = [pd.Timestamp(t, unit=u, tz="UTC") for t, u in zip(k.t, unit, strict=True)]
        k[["day", "close"]].drop_duplicates("day").sort_values("day").to_parquet(
            folder() / f"{sym}.parquet", index=False)
        print(f"{sym}: {len(k)} daily closes, {len(parts)}/{len(urls)} files")


def closes(symbol="BTCUSDT"):
    k = pd.read_parquet(folder() / f"{symbol}.parquet")
    # A daily candle opened on day D is known once it closes, at D + 1 day.
    return k.assign(known=(k.day + pd.Timedelta(days=1)).astype(C.UTC)).sort_values("known")


def ret30(times, symbol="BTCUSDT"):
    """30-day log return of the daily close known at each time (NaN without 30 days of data)."""
    k = closes(symbol)
    k["r30"] = np.log(k.close / k.close.shift(30))
    t = pd.DataFrame({"t": pd.Series(times).astype(C.UTC).reset_index(drop=True)}).reset_index()
    x = pd.merge_asof(t.sort_values("t"), k[["known", "r30", "close"]], left_on="t",
                      right_on="known", direction="backward")
    return x.sort_values("index").set_index("index")[["r30", "close"]]


def is_bull(times):
    return (ret30(times).r30 > THRESHOLD).to_numpy()


def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("mode", choices=["fetch"])
    ap.add_argument("--start", default="2024-09")
    ap.add_argument("--end", default="2026-10-15")
    a = ap.parse_args()
    fetch(a.start, a.end)


if __name__ == "__main__":
    main()
