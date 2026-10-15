"""Round 3: would a newcomer's 99¢ bid on a decided weather contract be filled? Pairs the live
top-of-book samples (kx_live_wx.py) with each market's trade tape and settlement.

At each sampled minute where the eventual winning side is bid at 99¢, a hypothetical order of
size q joins the back of that queue (size Q ahead, from the snapshot). It fills only once the
contracts sold into 99¢ bids afterwards exceed Q (FIFO; cancellations ahead are ignored, which
understates fills). Using the settled result to pick the side is look-ahead for the signal but
not for the fill mechanics, which is all this measures; M1/L1 cover whether the side is known.

Usage: python -I wx_queue.py fetch <books.csv>
       python -I wx_queue.py analyse <books.csv> [--size Q,...]
"""

import argparse
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "data"))
import kalshi_api


def paths(books):
    p = pathlib.Path(books)
    return p.with_name(p.stem + "_trades.parquet"), p.with_name(p.stem + "_results.parquet")


def fetch(books):
    b = pd.read_csv(books, usecols=["ticker"])
    tickers = sorted(b.ticker.unique())
    lim = kalshi_api.Limiter(5)
    res, parts = [], []
    for i, t in enumerate(tickers):
        m = kalshi_api.get(f"/markets/{t}", lim).get("market", {})
        res.append((t, m.get("result"), m.get("status")))
        _, rows = kalshi_api.trades(t, lim)
        cols = ["trade_id", "created_time", "yes_price", "count", "taker_side"]
        parts.append(pd.DataFrame(rows, columns=cols).assign(ticker=t))
        if i % 100 == 0:
            print(f"{i}/{len(tickers)} markets", flush=True)
    tp, rp = paths(books)
    pd.concat(parts, ignore_index=True).to_parquet(tp, index=False)
    pd.DataFrame(res, columns=["ticker", "result", "status"]).to_parquet(rp, index=False)
    print(f"saved {len(tickers)} markets", flush=True)


def analyse(books, sizes):
    tp, rp = paths(books)
    b = pd.read_csv(books)
    b["ts"] = pd.to_datetime(b.ts, utc=True)
    r = pd.read_parquet(rp)
    r = r[r.result.isin(["yes", "no"])].set_index("ticker").result
    t = pd.read_parquet(tp)
    t["ts"] = pd.to_datetime(t.created_time, utc=True, format="ISO8601")
    b = b[b.ticker.isin(r.index)].assign(win=lambda d: d.ticker.map(r))
    # Queue at 99¢ on the winning side: YES bid of 0.99, or NO bid of 0.99 (= YES ask of 0.01).
    yes = (b.win == "yes") & (b.yes_bid_dollars >= 0.985)
    no = (b.win == "no") & (b.yes_ask_dollars <= 0.015)
    b = b[yes | no].assign(queue=np.where(yes[yes | no], b.yes_bid_size_fp, b.yes_ask_size_fp))
    # Contracts sold into the winning side's 99¢ bid: the taker bought the losing side at 1¢.
    t = t.assign(win=t.ticker.map(r)).dropna(subset=["win"])
    p_lose = np.where(t.win == "yes", 1 - t.yes_price, t.yes_price)
    t = t[(t.taker_side != t.win) & (p_lose <= 0.015)].sort_values("ts")
    rows = []
    for tk, g in b.groupby("ticker"):
        tt = t[t.ticker == tk]
        cum = tt["count"].cumsum().to_numpy()
        total = cum[-1] if len(cum) else 0.0
        before = np.searchsorted(tt.ts.to_numpy(), g.ts.to_numpy(), side="right")
        sold_before = np.where(before > 0, cum[np.maximum(before - 1, 0)], 0.0)
        after = total - sold_before
        for q in sizes:
            filled = np.clip(after - g["queue"].to_numpy(), 0, q)
            rows.append(
                pd.DataFrame(
                    {
                        "ticker": tk,
                        "ts": g.ts,
                        "q": q,
                        "queue": g["queue"],
                        "after": after,
                        "filled": filled,
                    }
                )
            )
    d = pd.concat(rows, ignore_index=True)
    first = d.sort_values("ts").groupby(["ticker", "q"]).first().reset_index()
    for q in sizes:
        x, f = d[d.q == q], first[first.q == q]
        print(f"order size {q}: {x.ticker.nunique()} markets with a 99¢ queue on the winning side")
        print(
            f"  joining at a random sampled minute: any fill {(x.filled > 0).mean():.1%}, "
            f"mean fill {x.filled.mean() / q:.1%}; queue ahead median {x['queue'].median():,.0f}, "
            f"sold into it afterwards median {x['after'].median():,.0f}"
        )
        print(
            f"  joining at the first sampled minute: any fill {(f.filled > 0).mean():.1%}, "
            f"mean fill {f.filled.mean() / q:.1%}, ≈ ${(f.filled * 0.01).sum():,.2f} gross in total"
        )


def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("mode", choices=["fetch", "analyse"])
    ap.add_argument("books")
    ap.add_argument("--size", default="100,1000")
    a = ap.parse_args()
    if a.mode == "fetch":
        fetch(a.books)
    else:
        analyse(a.books, [float(x) for x in a.size.split(",")])


if __name__ == "__main__":
    main()
