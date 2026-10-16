"""Idea 3c: maker orders on Kalshi BTC 15-minute markets. At t0 = close - h, rest a bid at the YES
best bid (buys YES) and an offer at the YES best ask (buys NO at 1 - ask). Filled only if a later
1-minute candle before the close traded through the price (trade low < bid, or trade high > ask).
Held to resolution. Kalshi maker fee: 0 or 0.0175·p·(1−p) (charged on some series; not rounded,
i.e. assuming size).

Usage: python -I kalshi_maker.py explore
       python -I kalshi_maker.py holdout --horizon H --side S --lo X --hi Y
"""

import argparse
import gzip
import json
import pathlib
import sys

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common as C

HORIZONS = (12, 10, 5, 3, 2)


def num(d, k):
    d = d or {}
    v = d.get(k, d.get(f"{k}_dollars"))
    return None if v is None else float(v)


def load():
    m, cut = C.markets("kalshi")
    by_ticker = m.set_index("market_id")
    rows = []
    path = C.RAW / "github_kalshi/data/raw/kalshi_btc15/candlesticks.jsonl.gz"
    with gzip.open(path, "rt") as f:
        for line in f:
            d = json.loads(line)
            tk = d["endpoint"].split("/")[-2]
            if tk not in by_ticker.index:
                continue
            mk = by_ticker.loc[tk]
            close = int(mk.close_ts.timestamp())
            cs = {c["end_period_ts"]: c for c in d["response"]["candlesticks"]}
            for h in HORIZONS:
                c0 = cs.get(close - 60 * h)
                if c0 is None:
                    continue
                bid, ask = num(c0["yes_bid"], "close"), num(c0["yes_ask"], "close")
                later = [cs[t] for t in sorted(cs) if close - 60 * h < t <= close]
                lows = [num(c["price"], "low") for c in later if num(c["price"], "low") is not None]
                highs = [
                    num(c["price"], "high") for c in later if num(c["price"], "high") is not None
                ]
                rows.append(
                    {
                        "mkey": mk.mkey,
                        "market_id": tk,
                        "horizon": f"{h}m",
                        "bid": bid,
                        "ask": ask,
                        "min_after": min(lows) if lows else None,
                        "max_after": max(highs) if highs else None,
                        "y": mk.y,
                        "split": mk.split,
                        "cluster": mk.close_ts.strftime("%Y-%m-%d"),
                    }
                )
    s = pd.DataFrame(rows)
    s = s[(s.bid > 0) & (s.ask < 1) & (s.bid < s.ask)]
    s["mid"] = (s.bid + s.ask) / 2
    print(f"kalshi: split at close {cut}")
    return s


def orders(s, side, lo, hi, fee_rate):
    if side == "fav":
        # Symmetric: bid on YES when YES mid in [lo, hi); offer on YES (= bid on NO) when NO mid is.
        s1, f1 = orders(s, "yes", lo, hi, fee_rate)
        s2, f2 = orders(s, "no", 1 - hi, 1 - lo, fee_rate)
        return pd.concat([s1, s2]), pd.concat([f1, f2])
    sel = s[(s.mid >= lo) & (s.mid < hi)]
    if side == "yes":
        f = sel[sel.min_after < sel.bid].assign(price=lambda d: d.bid, y=lambda d: d.y)
    else:
        f = sel[sel.max_after > sel.ask].assign(price=lambda d: 1 - d.ask, y=lambda d: 1 - d.y)
    fee = fee_rate * f.price * (1 - f.price)
    return sel, f.assign(ret=(f.y - f.price - fee) / (f.price + fee))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["explore", "holdout"])
    ap.add_argument("--horizon")
    ap.add_argument("--side")
    ap.add_argument("--lo", type=float)
    ap.add_argument("--hi", type=float)
    ap.add_argument("--fee", type=float, default=0.0175)
    ap.add_argument("--alpha", type=float, default=0.05)
    a = ap.parse_args()
    s = load()
    if a.mode == "holdout":
        h = s[(s.split == "holdout") & (s.horizon == a.horizon)]
        sel, f = orders(h, a.side, a.lo, a.hi, a.fee)
        print(
            f"HOLDOUT kalshi maker {a.side} {a.horizon} mid in [{a.lo},{a.hi}) fee {a.fee}: orders "
            f"{len(sel)}, fill rate {len(f) / max(len(sel), 1):.3f}, "
            f"{C.fmt(C.summarise(f, alpha=a.alpha))}"
        )
        return
    e = s[s.split == "explore"]
    rows = []
    for h in sorted(e.horizon.unique(), key=lambda x: -int(x[:-1])):
        for side in ("yes", "no"):
            for lo, hi in ((0.02, 0.1), (0.1, 0.3), (0.3, 0.7), (0.7, 0.9), (0.9, 0.98)):
                sel, f0 = orders(e[e.horizon == h], side, lo, hi, 0.0)
                _, f1 = orders(e[e.horizon == h], side, lo, hi, 0.0175)
                s0, s1 = C.summarise(f0), C.summarise(f1)
                rows.append(
                    {
                        "horizon": h,
                        "buy": side,
                        "yes_mid": f"[{lo},{hi})",
                        "orders": len(sel),
                        "fill_rate": len(f0) / max(len(sel), 1),
                        "ret_nofee": s0.get("ret"),
                        "lo": s0.get("lo"),
                        "hi": s0.get("hi"),
                        "ret_fee": s1.get("ret"),
                        "fee_lo": s1.get("lo"),
                    }
                )
    print("exploration: Kalshi maker, filled on trade-through, held to resolution")
    print(pd.DataFrame(rows).to_string(index=False, float_format=lambda v: f"{v:.4f}"))


if __name__ == "__main__":
    main()
