"""Round 3: live cross-market check between Kalshi's hourly crypto ladder (KXBTCD / KXETHD:
"60-second BRTI average before the hour is above K") and the 15-minute up/down market closing on
the same hour (KXBTC15M / KXETH15M: "the same average is at least K*", K* = the average 15 minutes
earlier). Both settle on the identical number, so for ladder strikes K < K* the hourly YES is
implied by the 15-minute YES, and for K >= K* the hourly YES implies it. Hence, riskless:
- `buy_ladder`: 15-min YES bid > ask of "above K" for some K < K* (buy above-K, sell 15-min);
- `buy_15m`: bid of "above K" for some K >= K* > 15-min YES ask (buy 15-min, sell above-K).
Every poll in the last 15 minutes of each hour reads live order books (the 15-minute market
before and after the two ladder strikes bracketing K*) and logs them with any riskless edge,
before and after the 0.07·p·(1−p) taker fee on both legs.

Usage: python -I kx_live_xmkt.py <out.csv> [--minutes M] [--every S]
"""

import argparse
import csv
import json
import pathlib
import subprocess
import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

BASE = "https://api.elections.kalshi.com/trade-api/v2"
NY = ZoneInfo("America/New_York")
ASSETS = ("BTC", "ETH")
FEE = 0.07


def get(path):
    for i in range(4):
        r = subprocess.run(
            ["curl", "-s", "-m", "20", "-w", "\n%{http_code}", BASE + path],
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
        time.sleep(1.5 if code == "429" else 2**i)
    return None


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def best(levels):
    return max(((f(p), f(q)) for p, q in levels), default=(None, 0.0))


def book(ticker):
    """Live best YES bid / ask and sizes from the order book (the market listing's top of book
    is cached and lags by up to tens of seconds, which fakes crossings)."""
    d = get(f"/markets/{ticker}/orderbook")
    o = (d or {}).get("orderbook_fp") or {}
    yes, no = o.get("yes_dollars") or [], o.get("no_dollars") or []
    (bid, bsz), (nb, asz) = best(yes), best(no)
    return bid, (None if nb is None else round(1 - nb, 4)), bsz, asz


def fee(p):
    return FEE * p * (1 - p)


SETUP = {}


def setup(asset, close):
    """15-minute ticker and K*, and the ladder strikes just below and at/above K* (cached)."""
    key = (asset, close)
    if key in SETUP:
        return SETUP[key]
    code = close.astimezone(NY).strftime("%y%b%d%H").upper()
    q = get(f"/markets?series_ticker=KX{asset}15M&status=open&limit=20")
    ladder = get(f"/markets?event_ticker=KX{asset}D-{code}&limit=1000")
    want = close.isoformat().replace("+00:00", "Z")
    fif = [m for m in (q or {}).get("markets", []) if m.get("close_time") == want]
    if not fif or f(fif[0].get("floor_strike")) is None or not ladder:
        return None
    k_star = f(fif[0]["floor_strike"])
    legs = sorted(
        (f(m["floor_strike"]), m["ticker"])
        for m in ladder.get("markets", [])
        if m.get("strike_type") == "greater" and f(m.get("floor_strike")) is not None
    )
    below = [x for x in legs if x[0] < k_star]
    above = [x for x in legs if x[0] >= k_star]
    if not below or not above:
        return None
    SETUP[key] = (fif[0]["ticker"], k_star, below[-1], above[0])
    return SETUP[key]


def poll(asset, close):
    """Order books of the 15-minute market (read before and after the ladder legs), the ladder
    strike just below K* (cheapest implied leg) and the one at/above K* (richest implying leg).
    A crossing counts only if it holds against both 15-minute reads."""
    s = setup(asset, close)
    if s is None:
        return [], []
    t15, k_star, (k1, t1), (k2, t2) = s
    first = book(t15)
    lo, hi = book(t1), book(t2)
    second = book(t15)
    rows = [(t15, k_star, *first), (t1, k1, *lo), (t2, k2, *hi), (t15, k_star, *second)]
    edges = []
    bids15 = [x[0] for x in (first, second)]
    asks15 = [x[1] for x in (first, second)]
    if None not in bids15 and lo[1] is not None and min(bids15) > lo[1]:
        b15, ask = min(bids15), lo[1]
        g = b15 - ask
        depth = min(lo[3], first[2], second[2])
        edges.append(("buy_ladder", t1, g, g - fee(ask) - fee(1 - b15), depth))
    if None not in asks15 and hi[0] is not None and hi[0] > max(asks15):
        a15, bid = max(asks15), hi[0]
        g = bid - a15
        depth = min(hi[2], first[3], second[3])
        edges.append(("buy_15m", t2, g, g - fee(a15) - fee(1 - bid), depth))
    return rows, edges


def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("out")
    ap.add_argument("--minutes", type=float, default=180)
    ap.add_argument("--every", type=float, default=5)
    a = ap.parse_args()
    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    books = out.with_name(out.stem + "_books.csv")
    end = time.monotonic() + 60 * a.minutes
    with open(out, "a", newline="") as fe, open(books, "a", newline="") as fb:
        we, wb = csv.writer(fe), csv.writer(fb)
        if fe.tell() == 0:
            we.writerow(["ts", "asset", "kind", "ticker", "gross", "net", "depth"])
        if fb.tell() == 0:
            wb.writerow(["ts", "asset", "ticker", "strike", "bid", "ask", "bid_size", "ask_size"])
        while time.monotonic() < end:
            now = datetime.now(UTC)
            close = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
            if (close - now).total_seconds() > 15 * 60 - 20:
                time.sleep(10)
                continue
            ts = now.isoformat(timespec="seconds")
            for asset in ASSETS:
                rows, edges = poll(asset, close)
                wb.writerows((ts, asset, *r) for r in rows)
                we.writerows((ts, asset, *e) for e in edges)
                for e in edges:
                    print(
                        f"{ts} {asset} {e[0]} {e[1]} gross {e[2]:.2f} net {e[3]:.3f} "
                        f"depth {e[4]:.0f}",
                        flush=True,
                    )
            fe.flush()
            fb.flush()
            time.sleep(max(0.0, a.every - (datetime.now(UTC) - now).total_seconds()))


if __name__ == "__main__":
    main()
