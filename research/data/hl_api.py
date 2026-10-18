"""Download public Hyperliquid data for round 7 (PROTOCOL_V7.md, Amendments 1-2): the leaderboard,
the top accounts' fills, role and positions, funding and 1h candles, plus the Binance USDⓈ-M 1h
klines the backtest uses (Hyperliquid serves only each coin's latest 5,000 candles). No key
needed. Responses are untrusted data: parsed as JSON, never executed. Resumable; requests are
paced to Hyperliquid's weight budget and every non-200 reply is retried. One line per result.

Layout under <raw_root>/hyperliquid/: leaderboard.parquet, _source.json, wallets.parquet,
fills/<address>.parquet, funding/<coin>.parquet, candles/<coin>.parquet,
binance_um_1h/<coin>.parquet.

Usage:
  python -I hl_api.py <raw> leaderboard
  python -I hl_api.py <raw> wallets --top 200 --start 2026-07-20 --need-before 2026-09-12
  python -I hl_api.py <raw> funding --coins BTC,ETH --start 2024-01-01 --end 2026-10-18
  python -I hl_api.py <raw> candles --coins BTC,ETH --start 2026-06-21 --end 2026-10-18
  python -I hl_api.py <raw> binance --coins BTC,ETH --start 2023-12-01 --end 2026-07-20
"""

import argparse
import collections
import datetime as dt
import io
import json
import pathlib
import subprocess
import sys
import time
import zipfile

import pandas as pd

INFO = "https://api.hyperliquid.xyz/info"
LEADERBOARD = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
VISION = "https://data.binance.vision/data/futures/um"
BUDGET = 900  # weight per rolling minute; Hyperliquid allows 1,200 per IP
FILL_PAGE = 2000
FILL_CAP = 10_000
MAX_PAGES = 100  # 200,000 fills before need_before: treated as truncated
FUNDING_PAGE = 500
HOUR_MS = 3_600_000
FILL_COLS = {"time": "int64", "coin": str, "px": float, "sz": float, "side": str, "dir": str,
             "startPosition": float, "closedPnl": float, "fee": float, "feeToken": str,
             "crossed": bool, "oid": "int64", "tid": "int64"}


class Weights:
    """Rolling one-minute weight budget: wait before a request until its weight fits."""

    def __init__(self, budget):
        self.budget, self.spent = budget, collections.deque()

    def _used(self):
        while self.spent and self.spent[0][0] < time.monotonic() - 60:
            self.spent.popleft()
        return sum(w for _, w in self.spent)

    def wait(self, weight):
        while self._used() + weight > self.budget:
            time.sleep(1)
        self.add(weight)

    def add(self, weight):
        self.spent.append((time.monotonic(), weight))


WEIGHTS = Weights(BUDGET)


def curl(args, tries=12):
    """(body, HTTP code) of the first 200 reply; anything else is retried with backoff."""
    code = ""
    for i in range(tries):
        r = subprocess.run(["curl", "-s", "-m", "120", "-w", "\n%{http_code}", *args],
                           capture_output=True, text=True, check=False)
        body, _, code = r.stdout.rpartition("\n")
        if code == "200":
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                pass
        time.sleep(min(60, 2**i))
    sys.exit(f"{args[-1]} failed after {tries} tries (last HTTP {code})")


def info(body, weight=20, per=None):
    """POST to /info; `per` adds one weight per that many items returned (Hyperliquid's rule)."""
    WEIGHTS.wait(weight)
    out = curl(["-H", "Content-Type: application/json", "-d", json.dumps(body), INFO])
    if per and isinstance(out, list):
        WEIGHTS.add(len(out) // per)
    return out


def ms(day):
    return int(pd.Timestamp(day, tz="UTC").timestamp() * 1000)


def coin_file(folder, coin):
    return folder / f"{coin.replace(':', '_')}.parquet"


def leaderboard(root):
    rows = []
    for r in curl([LEADERBOARD])["leaderboardRows"]:
        w = {k: v for k, v in r["windowPerformances"]}
        rows.append({
            "address": r["ethAddress"].lower(), "account_value": float(r["accountValue"]),
            "month_pnl": float(w["month"]["pnl"]), "month_vlm": float(w["month"]["vlm"]),
            "all_pnl": float(w["allTime"]["pnl"]), "name": r.get("displayName"),
        })
    lb = pd.DataFrame(rows)
    lb.to_parquet(root / "leaderboard.parquet", index=False)
    now = dt.datetime.now(dt.UTC)
    (root / "_source.json").write_text(json.dumps(
        {"fetched_at": now.isoformat(), "run_date": now.strftime("%Y-%m-%d"),
         "leaderboard": LEADERBOARD, "accounts": len(lb)}, indent=1))
    print(f"leaderboard: {len(lb):,} accounts, fetched {now:%Y-%m-%d %H:%M} UTC")


def fills(user, t0, need_before=None):
    """Every fill from t0 to need_before (or now) that Hyperliquid still serves; it drops the
    oldest fills of busy accounts. Returns (fills, late, capped): `late` when even the oldest
    served fill is after need_before, `capped` when MAX_PAGES did not reach need_before."""
    out, seen, t = [], set(), t0
    stop = need_before or int(time.time() * 1000)
    for i in range(MAX_PAGES):
        page = info({"type": "userFillsByTime", "user": user, "startTime": t,
                     "endTime": int(time.time() * 1000)}, per=20)
        new = [f for f in page if (f["tid"], f["oid"]) not in seen]
        seen.update((f["tid"], f["oid"]) for f in new)
        out += new
        if i == 0 and page and page[0]["time"] > stop:
            return _frame(out), True, False
        if len(page) < FILL_PAGE or not new or page[-1]["time"] >= stop:
            return _frame(out), False, False
        t = page[-1]["time"]
    return _frame(out), False, True


def _frame(out):
    f = pd.DataFrame([{k: x.get(k) for k in FILL_COLS} for x in out], columns=list(FILL_COLS))
    return f.astype(FILL_COLS).rename(columns={"startPosition": "start_pos",
                                               "closedPnl": "closed_pnl", "feeToken": "fee_token"})


def wallets(root, top, start, need_before):
    lb = pd.read_parquet(root / "leaderboard.parquet").nlargest(top, "month_pnl")
    folder = root / "fills"
    folder.mkdir(exist_ok=True)
    mp = root / "wallets.parquet"
    meta = pd.read_parquet(mp) if mp.exists() else pd.DataFrame(columns=["address"])
    rows = meta.to_dict("records")
    for a in lb.address:
        if a in set(meta.address):
            continue
        f, late, capped = fills(a, ms(start), ms(need_before) if need_before else None)
        f.to_parquet(folder / f"{a}.parquet", index=False)
        role = info({"type": "userRole", "user": a}, weight=60).get("role")
        pos = info({"type": "clearinghouseState", "user": a}, weight=2).get("assetPositions", [])
        lev = [float(p["position"]["leverage"]["value"]) for p in pos]
        old = len(f) and f.time.min() > ms(start) + 24 * HOUR_MS
        rows.append({"address": a, "role": role, "fills": len(f), "late": late,
                     "truncated": bool((len(f) >= FILL_CAP and old) or capped),
                     "first_fill": f.time.min(),
                     "leverage": pd.Series(lev).median() if lev else None})
        pd.DataFrame(rows).to_parquet(mp, index=False)
    w = pd.DataFrame(rows)
    print(f"wallets: {len(w)} of top {top}, {int(w.truncated.sum())} truncated (oldest lost, or "
          f"> {MAX_PAGES * FILL_PAGE:,} fills), {int(w.late.sum())} with no fill served before "
          f"{need_before}, {int((w.role == 'vault').sum())} vaults")


def funding(root, coins, start, end):
    folder = root / "funding"
    folder.mkdir(exist_ok=True)
    for coin in coins:
        p = coin_file(folder, coin)
        old = pd.read_parquet(p) if p.exists() else pd.DataFrame(columns=["time", "rate"])
        rows, t, t1 = [], ms(start), ms(end)
        if len(old) and old.time.min() <= t + HOUR_MS:  # already covers the start: resume
            t = max(t, int(old.time.max()) + 1)
        while t < t1:
            page = info({"type": "fundingHistory", "coin": coin, "startTime": t, "endTime": t1},
                        per=20)
            if not page:
                break
            rows += [(r["time"], float(r["fundingRate"])) for r in page]
            t = page[-1]["time"] + 1
        f = pd.concat([old, pd.DataFrame(rows, columns=["time", "rate"])], ignore_index=True)
        f = f.astype({"time": "int64"}).drop_duplicates("time").sort_values("time")
        f.to_parquet(p, index=False)
        span = (f" {pd.Timestamp(f.time.min(), unit='ms'):%Y-%m-%d} → "
                f"{pd.Timestamp(f.time.max(), unit='ms'):%Y-%m-%d}") if len(f) else ""
        print(f"funding {coin}: {len(f):,} prints{span}")


def candles(root, coins, start, end):
    folder = root / "candles"
    folder.mkdir(exist_ok=True)
    for coin in coins:
        c = info({"type": "candleSnapshot", "req": {"coin": coin, "interval": "1h",
                  "startTime": ms(start), "endTime": ms(end)}}, per=60)
        k = pd.DataFrame([(r["t"], float(r["o"]), float(r["c"])) for r in c],
                         columns=["t", "open", "close"])
        k.to_parquet(coin_file(folder, coin), index=False)
        first = f" from {pd.Timestamp(k.t.min(), unit='ms'):%Y-%m-%d}" if len(k) else ""
        print(f"candles {coin}: {len(k):,} hours{first}")


def _vision(url):
    r = subprocess.run(["curl", "-sf", "-m", "120", url], capture_output=True, check=False)
    if r.returncode:
        return None
    with zipfile.ZipFile(io.BytesIO(r.stdout)) as z:
        k = pd.read_csv(z.open(z.namelist()[0]), header=None, usecols=[0, 1, 4],
                        names=["t", "open", "close"], dtype=str)
    k = k[k.t.str.isdigit()]
    return k.astype({"t": "int64", "open": float, "close": float})


def binance_symbol(coin):
    """Binance USDⓈ-M symbol of a Hyperliquid coin ("kPEPE" is 1000PEPE), or None for HIP-3."""
    if ":" in coin:
        return None
    return ("1000" + coin[1:] if coin.startswith("k") and coin[1:].isupper() else coin) + "USDT"


def binance(root, coins, start, end):
    folder = root / "binance_um_1h"
    folder.mkdir(exist_ok=True)
    last = pd.Timestamp(end, tz="UTC")
    months = pd.period_range(pd.Timestamp(start).strftime("%Y-%m"),
                             (last - pd.offsets.MonthBegin(1)).strftime("%Y-%m"), freq="M")
    days = pd.date_range(last.replace(day=1), last - pd.Timedelta(days=1), freq="D")
    for coin in coins:
        sym = binance_symbol(coin)
        if sym is None:
            print(f"binance {coin}: no USDⓈ-M perp (HIP-3), skipped")
            continue
        urls = [f"{VISION}/monthly/klines/{sym}/1h/{sym}-1h-{m}.zip" for m in months]
        urls += [f"{VISION}/daily/klines/{sym}/1h/{sym}-1h-{d:%Y-%m-%d}.zip" for d in days]
        parts = [p for p in map(_vision, urls) if p is not None]
        if not parts:
            print(f"binance {coin}: no {sym} files")
            continue
        k = pd.concat(parts).drop_duplicates("t").sort_values("t")
        k = k[k.t < ms(end)]
        k.to_parquet(coin_file(folder, coin), index=False)
        print(f"binance {coin}: {len(k):,} hours from "
              f"{pd.Timestamp(k.t.min(), unit='ms'):%Y-%m-%d}, {len(parts)}/{len(urls)} files")


def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("raw_root")
    ap.add_argument("mode", choices=["leaderboard", "wallets", "funding", "candles", "binance"])
    ap.add_argument("--top", type=int, default=200)
    ap.add_argument("--coins", default="BTC,ETH,SOL")
    ap.add_argument("--start", default="2026-07-20")
    ap.add_argument("--end", default="2026-10-18")
    ap.add_argument("--need-before", default="2026-09-12")
    a = ap.parse_args()
    root = pathlib.Path(a.raw_root) / "hyperliquid"
    root.mkdir(parents=True, exist_ok=True)
    coins = a.coins.split(",")
    if a.mode == "leaderboard":
        leaderboard(root)
    elif a.mode == "wallets":
        wallets(root, a.top, a.start, a.need_before)
    elif a.mode == "funding":
        funding(root, coins, a.start, a.end)
    elif a.mode == "candles":
        candles(root, coins, a.start, a.end)
    else:
        binance(root, coins, a.start, a.end)


if __name__ == "__main__":
    main()
