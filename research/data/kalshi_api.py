"""Download settled Kalshi markets and their full trade tape from the public API (no key needed).

One folder per series under <raw_root>/kalshi_api/<SERIES>/: markets.parquet (settled markets
closing in the window), trades.parquet (every trade: time, YES price, size, taker side) and
_source.json. Resumable: markets whose trades are already saved are skipped. Rate-limited.
Responses are untrusted data: parsed as JSON, never executed.

Usage: python -I kalshi_api.py <raw_root> --series A,B,... --start 2026-08-15 --end 2026-10-15
       [--rps 10] [--threads 8] [--min-volume 1] [--markets-only]
       [--close-window A,B] [--occurrence-window A,B] [--sample N] [--historical]

Markets settled before Kalshi's historical cutoff (GET /historical/cutoff) are served only by
/historical/markets and /historical/trades; --historical reads those instead.
"""

import argparse
import datetime as dt
import json
import pathlib
import subprocess
import sys
import threading
import time
import urllib.parse
import zlib
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

BASE = "https://api.elections.kalshi.com/trade-api/v2"
EPOCH = pd.Timestamp(0, tz="UTC")
MARKET_COLS = [
    "ticker",
    "event_ticker",
    "title",
    "yes_sub_title",
    "no_sub_title",
    "open_time",
    "close_time",
    "expected_expiration_time",
    "occurrence_datetime",
    "settlement_ts",
    "result",
    "floor_strike",
    "cap_strike",
    "strike_type",
    "volume_fp",
    "settlement_value_dollars",
    "expiration_value",
    "rules_primary",
]


class Limiter:
    def __init__(self, rps):
        self.gap, self.next, self.lock = 1.0 / rps, 0.0, threading.Lock()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            t = max(now, self.next)
            self.next = t + self.gap
        time.sleep(max(0.0, t - now))


def get(path, limiter, tries=20):
    code = ""
    for i in range(tries):
        limiter.wait()
        r = subprocess.run(
            ["curl", "-s", "-m", "60", "-w", "\n%{http_code}", BASE + path],
            capture_output=True,
            text=True,
            check=False,
        )
        body, _, code = r.stdout.rpartition("\n")
        if code == "200":
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                pass
        # Rate limited: brief pause; anything else: exponential backoff.
        time.sleep(1.5 if code == "429" else min(30, 2**i))
    sys.exit(f"GET {path} failed after {tries} tries (last HTTP {code})")


def settled_markets(series, t0, t1, limiter, historical=False):
    """Settled markets closing in [t0, t1). Kalshi serves markets settled before its historical
    cutoff (GET /historical/cutoff) only from /historical/markets, filtered here by close time."""
    out, cur = [], ""
    while True:
        if historical:
            q = f"/historical/markets?series_ticker={series}&limit=200"
        else:
            q = (
                f"/markets?series_ticker={series}&status=settled&limit=1000"
                f"&min_close_ts={t0}&max_close_ts={t1}"
            )
        d = get(q + (f"&cursor={cur}" if cur else ""), limiter)
        out += d.get("markets", [])
        cur = d.get("cursor")
        if not cur or not d.get("markets"):
            break
    if historical:
        close = pd.to_datetime([m["close_time"] for m in out], utc=True, format="ISO8601")
        secs = (close - EPOCH) // pd.Timedelta(seconds=1)
        out = [m for m, c in zip(out, secs, strict=True) if t0 <= c < t1]
    return out


def trades(ticker, limiter, min_ts=None, max_ts=None, historical=False):
    out, cur = [], ""
    win = (f"&min_ts={min_ts}" if min_ts else "") + (f"&max_ts={max_ts}" if max_ts else "")
    path = "/historical/trades" if historical else "/markets/trades"
    while True:
        tk = urllib.parse.quote(ticker, safe="")  # a few old tickers contain spaces
        q = f"{path}?ticker={tk}&limit=1000{win}" + (f"&cursor={cur}" if cur else "")
        d = get(q, limiter)
        out += [
            (
                t["trade_id"],
                t["created_time"],
                float(t["yes_price_dollars"]),
                float(t.get("count_fp") or t.get("count") or 0),
                t["taker_side"],
            )
            for t in d.get("trades", [])
        ]
        cur = d.get("cursor")
        if not cur or not d.get("trades"):
            return ticker, out


def window(m, close_window, occurrence_window):
    """Per-market (min_ts, max_ts) in unix seconds (None = unbounded). close_window (A, B): trades
    from A to B hours before close; occurrence_window (A, B): the same relative to
    occurrence_datetime (else expected_expiration_time, which older markets carry instead, else
    close_time). A may be None (no lower bound)."""

    def ts(col):
        v = m[col] if col in m else pd.Series(None, index=m.index, dtype=object)
        return pd.to_datetime(v, utc=True, format="ISO8601", errors="coerce")

    close = ts("close_time")
    occ = ts("occurrence_datetime").fillna(ts("expected_expiration_time")).fillna(close)
    lo = hi = pd.Series([None] * len(m), index=m.index, dtype=object)
    for spec, ref in ((close_window, close), (occurrence_window, occ)):
        if spec is None:
            continue
        a, b = spec
        if a is not None:
            lo = ((ref - pd.Timedelta(hours=a) - EPOCH) // pd.Timedelta(seconds=1)).astype(object)
        hi = ((ref - pd.Timedelta(hours=b) - EPOCH) // pd.Timedelta(seconds=1)).astype(object)
    return dict(zip(m.ticker, zip(lo, hi, strict=True), strict=True))


def fetch_series(
    series,
    root,
    t0,
    t1,
    limiter,
    threads,
    min_volume,
    markets_only=False,
    close_window=None,
    occurrence_window=None,
    sample=None,
    historical=False,
):
    folder = root / "kalshi_api" / series
    folder.mkdir(parents=True, exist_ok=True)
    ms = settled_markets(series, t0, t1, limiter, historical)
    if not ms:
        print(f"{series}: no settled markets in window")
        return
    m = pd.DataFrame(ms)
    m = m[[c for c in MARKET_COLS if c in m]].assign(series=series)
    m["volume_fp"] = pd.to_numeric(m.volume_fp, errors="coerce").fillna(0)
    m.to_parquet(folder / "markets.parquet", index=False)
    if markets_only:
        print(f"{series}: saved {len(m)} markets (metadata only)", flush=True)
        return
    done_path = folder / "trades.parquet"
    have = (
        pd.read_parquet(done_path)
        if done_path.exists()
        else pd.DataFrame(
            columns=["trade_id", "created_time", "yes_price", "count", "taker_side", "ticker"]
        )
    )
    eligible = sorted(m.ticker[m.volume_fp >= min_volume])
    if sample and len(eligible) > sample:
        # Outcome-blind random sample, fixed per series so reruns resume the same set.
        rng = np.random.default_rng(zlib.crc32(series.encode()))
        eligible = sorted(rng.choice(eligible, size=sample, replace=False))
    todo = [t for t in eligible if t not in set(have.ticker)]
    print(f"{series}: {len(m)} markets, {len(todo)} need trades", flush=True)
    parts, n = [have], 0
    with ThreadPoolExecutor(max_workers=threads) as ex:
        win = window(m, close_window, occurrence_window)
        for ticker, rows in ex.map(lambda t: trades(t, limiter, *win[t], historical), todo):
            parts.append(
                pd.DataFrame(
                    rows, columns=["trade_id", "created_time", "yes_price", "count", "taker_side"]
                ).assign(ticker=ticker)
            )
            n += 1
            if n % 100 == 0:
                pd.concat(parts, ignore_index=True).to_parquet(done_path, index=False)
                print(f"  {series}: {n}/{len(todo)} markets", flush=True)
    t = pd.concat(parts, ignore_index=True)
    t.to_parquet(done_path, index=False)
    (folder / "_source.json").write_text(
        json.dumps(
            {
                "api": BASE,
                "series": series,
                "min_close_ts": t0,
                "max_close_ts": t1,
                "markets": len(m),
                "trades": len(t),
                "close_window_hours": close_window,
                "occurrence_window_hours": occurrence_window,
                "sample": sample,
                "historical": historical,
                "markets_with_trades": len(eligible),
                "fetched_at": dt.datetime.now(dt.UTC).isoformat(),
            },
            indent=1,
        )
    )
    print(f"{series}: saved {len(m)} markets, {len(t):,} trades", flush=True)


def pair(x):
    if not x:
        return None
    a, b = x.split(",")
    return (float(a) if a else None, float(b))


def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("raw_root")
    ap.add_argument("--series", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--rps", type=float, default=10)
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--min-volume", type=float, default=1)
    ap.add_argument("--markets-only", action="store_true", help="refresh market metadata only")
    ap.add_argument("--close-window", help="A,B: only trades A..B hours before close (A may be '')")
    ap.add_argument("--occurrence-window", help="A,B: only trades A..B hours before occurrence")
    ap.add_argument("--sample", type=int, help="random markets per series (outcome-blind)")
    ap.add_argument(
        "--historical", action="store_true", help="markets settled before Kalshi's cutoff"
    )
    a = ap.parse_args()
    ts = [
        int(dt.datetime.fromisoformat(f"{d}T00:00:00+00:00").timestamp()) for d in (a.start, a.end)
    ]
    limiter = Limiter(a.rps)
    for s in a.series.split(","):
        fetch_series(
            s,
            pathlib.Path(a.raw_root),
            ts[0],
            ts[1],
            limiter,
            a.threads,
            a.min_volume,
            a.markets_only,
            pair(a.close_window),
            pair(a.occurrence_window),
            a.sample,
            a.historical,
        )


if __name__ == "__main__":
    main()
