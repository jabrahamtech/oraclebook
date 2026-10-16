"""Round 3: forward scan of live Kalshi top-of-book for structural arbitrage (PROTOCOL_V3.md).

Each pass reads every open event with its markets (public API, top of book with sizes) and logs:
- `sell_all`: a mutually exclusive event whose YES bids sum above $1 — buying NO on every market
  costs sum(1 - bid) and pays at least n - 1;
- `buy_all`: YES asks summing below $1 — pays $1 only if the set is exhaustive (flagged, since
  Kalshi's mutually_exclusive flag does not promise that);
- `ladder`: ordered strikes in one event where a stronger condition bids above a weaker one's ask
  (e.g. YES ">K2" bid > YES ">K1" ask, K1 < K2): buy the weaker, sell the stronger, payoff >= $1.
Edges are per contract set before and after the taker fee 0.07·p·(1−p) per leg (unrounded; Kalshi
rounds fees up per order, which only makes small edges worse). Depth = smallest leg size.

Usage: python -I kx_live_arb.py <out.csv> [--minutes M] [--rps R]
"""

import argparse
import csv
import json
import pathlib
import re
import subprocess
import time
from datetime import UTC, datetime

BASE = "https://api.elections.kalshi.com/trade-api/v2"
FEE = 0.07


def get(path, rps):
    for i in range(8):
        time.sleep(1 / rps)
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
        time.sleep(2 if code == "429" else min(30, 2**i))
    return None


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def fee(p):
    return FEE * p * (1 - p)


def book(m):
    bid, ask = num(m.get("yes_bid_dollars")), num(m.get("yes_ask_dollars"))
    bsz, asz = num(m.get("yes_bid_size_fp")) or 0, num(m.get("yes_ask_size_fp")) or 0
    return bid if bsz > 0 else None, ask if asz > 0 else None, bsz, asz


def scan_event(e, now):
    ms = [m for m in e.get("markets", []) if m.get("status") == "active"]
    if len(ms) < 2:
        return []
    out = []
    books = [book(m) for m in ms]
    if e.get("mutually_exclusive"):
        if all(b[0] is not None for b in books):
            s = sum(b[0] for b in books)
            net = s - 1 - sum(fee(1 - b[0]) for b in books)
            if s > 1:
                out.append(("sell_all", s - 1, net, min(b[2] for b in books), len(ms), ""))
        if all(b[1] is not None for b in books):
            s = sum(b[1] for b in books)
            net = 1 - s - sum(fee(b[1]) for b in books)
            if s < 1:
                out.append(
                    ("buy_all", 1 - s, net, min(b[3] for b in books), len(ms), "needs exhaustive")
                )
    else:
        groups = {}
        for m, b in zip(ms, books, strict=True):
            kind = m.get("strike_type")
            k = num(m.get("floor_strike" if kind == "greater" else "cap_strike"))
            if kind in ("greater", "less") and k is not None:
                # Same underlying = same custom_strike (e.g. one city of a multi-city event) and
                # the same title once its numbers are blanked out.
                title = re.sub(r"[\d.,$]+", "#", m.get("title", ""))
                u = (kind, json.dumps(m.get("custom_strike"), sort_keys=True), title)
                groups.setdefault(u, []).append((k, b, m["ticker"]))
        for (kind, _, _), leg in groups.items():
            # 'greater': higher strike is the stronger condition; 'less': lower strike is.
            leg.sort(key=lambda x: x[0], reverse=(kind == "less"))
            for i, (kw, weak, wt) in enumerate(leg):
                for ks, strong, st in leg[i + 1 :]:
                    if ks == kw:  # same strike, different date or condition: not ordered
                        continue
                    if weak[1] is not None and strong[0] is not None and strong[0] > weak[1]:
                        gross = strong[0] - weak[1]
                        net = gross - fee(weak[1]) - fee(1 - strong[0])
                        out.append(("ladder", gross, net, min(weak[3], strong[2]), 2, f"{wt}>{st}"))
    return [(now, e["event_ticker"], e.get("series_ticker", ""), *o) for o in out]


def scan(rps):
    now = datetime.now(UTC).isoformat(timespec="seconds")
    rows, cur, n_ev = [], "", 0
    while True:
        d = get(
            "/events?status=open&with_nested_markets=true&limit=200"
            + (f"&cursor={cur}" if cur else ""),
            rps,
        )
        if d is None:
            break
        for e in d.get("events", []):
            n_ev += 1
            rows += scan_event(e, now)
        cur = d.get("cursor")
        if not cur or not d.get("events"):
            break
    return now, n_ev, rows


def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("out")
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--rps", type=float, default=2)
    a = ap.parse_args()
    end = time.monotonic() + 60 * a.minutes
    pathlib.Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "a", newline="") as f:
        w = csv.writer(f)
        if f.tell() == 0:
            w.writerow(["scan", "event", "series", "kind", "gross", "net", "depth", "legs", "note"])
        while time.monotonic() < end:
            t0 = time.monotonic()
            now, n_ev, rows = scan(a.rps)
            w.writerows(rows)
            f.flush()
            pos = sum(r[5] > 0 for r in rows)
            print(
                f"{now}: {n_ev} events, {len(rows)} crossed, {pos} positive after fees "
                f"({time.monotonic() - t0:.0f}s)",
                flush=True,
            )


if __name__ == "__main__":
    main()
