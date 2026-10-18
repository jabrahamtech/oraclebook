"""Download resolved Polymarket crypto Up/Down markets and their taker trade tape from the public
APIs (no key needed): Gamma for the market and its resolution, data-api for every taker trade
with the wallet behind it.

One folder per series under <raw_root>/polymarket_api/<asset>-<minutes>m/: markets.parquet (one
row per resolved market), trades.parquet (every taker trade: time, token, side, price, size,
wallet) and _source.json. Resumable: markets whose trades are already saved are skipped.
Rate-limited. Responses are untrusted data: parsed as JSON, never executed.

Usage: python -I polymarket_api.py <raw_root> --assets BTC,ETH --minutes 5
       --start 2026-09-24 --end 2026-10-15 [--rps 8] [--threads 8]
"""

import argparse
import datetime as dt
import json
import pathlib
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

GAMMA = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
PAGE = 500
TRADE_COLS = ["ts", "token", "side", "price", "size", "wallet", "tx"]


class Limiter:
    def __init__(self, rps):
        self.gap, self.next, self.lock = 1.0 / rps, 0.0, threading.Lock()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            t = max(now, self.next)
            self.next = t + self.gap
        time.sleep(max(0.0, t - now))


def get(url, limiter, tries=12):
    """JSON body, or None on 404 / a 400 (data-api answers 400 past its deepest offset)."""
    code = ""
    for i in range(tries):
        limiter.wait()
        r = subprocess.run(
            ["curl", "-s", "-m", "60", "-w", "\n%{http_code}", url],
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
        if code in ("400", "404"):
            return None
        time.sleep(1.5 if code == "429" else min(30, 2**i))
    sys.exit(f"GET {url} failed after {tries} tries (last HTTP {code})")


def market(asset, minutes, start, limiter):
    """The resolved Up/Down market for one window, or None (missing, unresolved or malformed)."""
    slug = f"{asset.lower()}-updown-{minutes}m-{start}"
    for suffix in ("&closed=true", ""):
        d = get(f"{GAMMA}/markets?slug={slug}{suffix}", limiter)
        if d:
            break
    else:
        return None
    m = d[0]
    outcomes = [str(o).lower() for o in json.loads(m.get("outcomes") or "[]")]
    tokens = json.loads(m.get("clobTokenIds") or "[]")
    prices = [float(p) for p in json.loads(m.get("outcomePrices") or "[]")]
    if outcomes != ["up", "down"] or len(tokens) != 2 or len(prices) != 2:
        return None
    if prices[0] not in (0.0, 1.0) or prices[0] + prices[1] != 1.0:
        return None
    return {
        "condition_id": m["conditionId"],
        "slug": slug,
        "open_ts": pd.Timestamp(start, unit="s", tz="UTC"),
        "close_ts": pd.Timestamp(start + minutes * 60, unit="s", tz="UTC"),
        "up_token": tokens[0],
        "down_token": tokens[1],
        "y": prices[0],
        "volume": float(m.get("volumeNum") or 0.0),
    }


def trades(condition_id, limiter):
    """Every taker trade of a market (newest first from the API); truncated=True when data-api
    stopped paginating before the oldest trade."""
    out, offset = [], 0
    while True:
        d = get(
            f"{DATA_API}/trades?market={condition_id}&limit={PAGE}&offset={offset}"
            "&takerOnly=true",
            limiter,
        )
        if d is None:
            return condition_id, out, offset > 0
        out += [
            (
                int(t["timestamp"]),
                str(t["asset"]),
                str(t["side"]).upper(),
                float(t["price"]),
                float(t["size"]),
                str(t.get("proxyWallet", "")),
                str(t.get("transactionHash", "")),
            )
            for t in d
        ]
        if len(d) < PAGE:
            return condition_id, out, False
        offset += PAGE


def fetch_series(asset, minutes, root, t0, t1, limiter, threads):
    series = f"{asset.lower()}-{minutes}m"
    folder = root / "polymarket_api" / series
    folder.mkdir(parents=True, exist_ok=True)
    step = minutes * 60
    starts = range(t0 - t0 % step, t1 - step + 1, step)
    mpath = folder / "markets.parquet"
    have_m = pd.read_parquet(mpath) if mpath.exists() else pd.DataFrame()
    known = set(have_m.slug) if len(have_m) else set()
    todo = [s for s in starts if f"{asset.lower()}-updown-{minutes}m-{s}" not in known]
    with ThreadPoolExecutor(max_workers=threads) as ex:
        new = [r for r in ex.map(lambda s: market(asset, minutes, s, limiter), todo) if r]
    m = pd.concat([have_m, pd.DataFrame(new)], ignore_index=True)
    if m.empty:
        print(f"{series}: no resolved markets in window")
        return
    m.to_parquet(mpath, index=False)
    tpath = folder / "trades.parquet"
    have = pd.read_parquet(tpath) if tpath.exists() else pd.DataFrame(columns=TRADE_COLS)
    todo = sorted(set(m.condition_id) - set(have.get("condition_id", [])))
    print(f"{series}: {len(m)} markets, {len(todo)} need trades", flush=True)
    parts, truncated, n = [have], [], 0
    with ThreadPoolExecutor(max_workers=threads) as ex:
        for cid, rows, cut in ex.map(lambda c: trades(c, limiter), todo):
            parts.append(pd.DataFrame(rows, columns=TRADE_COLS).assign(condition_id=cid))
            if cut:
                truncated.append(cid)
            n += 1
            if n % 100 == 0:
                pd.concat(parts, ignore_index=True).to_parquet(tpath, index=False)
                print(f"  {series}: {n}/{len(todo)} markets", flush=True)
    t = pd.concat(parts, ignore_index=True)
    t.to_parquet(tpath, index=False)
    (folder / "_source.json").write_text(
        json.dumps(
            {
                "gamma": GAMMA,
                "data_api": DATA_API,
                "series": series,
                "min_open_ts": t0,
                "max_close_ts": t1,
                "markets": len(m),
                "trades": len(t),
                "truncated_markets_this_run": truncated,
                "fetched_at": dt.datetime.now(dt.UTC).isoformat(),
            },
            indent=1,
        )
    )
    print(f"{series}: saved {len(m)} markets, {len(t):,} trades", flush=True)


def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("raw_root")
    ap.add_argument("--assets", required=True)
    ap.add_argument("--minutes", type=int, default=5)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--rps", type=float, default=8)
    ap.add_argument("--threads", type=int, default=8)
    a = ap.parse_args()
    t0, t1 = (
        int(dt.datetime.fromisoformat(f"{d}T00:00:00+00:00").timestamp()) for d in (a.start, a.end)
    )
    limiter = Limiter(a.rps)
    for asset in a.assets.split(","):
        fetch_series(asset, a.minutes, pathlib.Path(a.raw_root), t0, t1, limiter, a.threads)


if __name__ == "__main__":
    main()
