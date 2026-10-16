"""Round 3: historical version of kx_live_xmkt.py from trade prints. For each sampled 15-minute
crypto market closing on the hour (round-2 tapes), fetch the hourly-ladder markets of the same
hour with strikes nearest K* (last 15 minutes of trades), then look for print pairs within
`--window` seconds that cross the no-arbitrage bound:
- a 15-minute taker sale of YES at b (a bid of b existed) and a ladder taker buy of "above K",
  K < K*, at a < b (an ask of a existed);
- a ladder taker sale of "above K", K >= K*, at b and a 15-minute taker buy of YES at a < b.
Prints are not simultaneous quotes, so this bounds how often the books were crossed.

Usage: python -I kx_xmkt_hist.py fetch
       python -I kx_xmkt_hist.py analyse [--window S]
"""

import argparse
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "data"))
import kalshi_api
import kx

OUT = kx.RAW.parent / "kalshi_api_xmkt"
NEAR = 3
FEE = 0.07


def pairs(asset):
    m15, _ = kx.load([f"KX{asset}15M"], trades=False)
    md, _ = kx.load([f"KX{asset}D"], trades=False)
    have = pd.read_parquet(kx.RAW / f"KX{asset}15M" / "trades.parquet", columns=["ticker"])
    m15 = m15[m15.ticker.isin(set(have.ticker)) & (m15.close_time.dt.minute == 0)]
    md = md[md.strike_type == "greater"]
    rows = []
    for _, r in m15.iterrows():
        lad = md[md.close_time == r.close_time].sort_values("floor_strike")
        below = lad[lad.floor_strike < r.floor_strike].tail(NEAR)
        above = lad[lad.floor_strike >= r.floor_strike].head(NEAR)
        for _, x in pd.concat([below, above]).iterrows():
            rows.append((r.ticker, r.floor_strike, r.close_time, x.ticker, x.floor_strike))
    return pd.DataFrame(rows, columns=["t15", "k_star", "close_time", "ticker", "strike"])


def fetch():
    lim = kalshi_api.Limiter(5)
    for asset in ("BTC", "ETH"):
        p = pairs(asset)
        folder = OUT / f"KX{asset}D"
        folder.mkdir(parents=True, exist_ok=True)
        p.to_parquet(folder / "pairs.parquet", index=False)
        parts = []
        for i, (tk, close) in enumerate(
            p.drop_duplicates("ticker")[["ticker", "close_time"]].itertuples(index=False)
        ):
            hi = int((close - kalshi_api.EPOCH) // pd.Timedelta(seconds=1))
            _, rows = kalshi_api.trades(tk, lim, hi - 15 * 60, hi)
            parts.append(
                pd.DataFrame(
                    rows, columns=["trade_id", "created_time", "yes_price", "count", "taker_side"]
                ).assign(ticker=tk)
            )
            if i % 100 == 0:
                print(f"{asset}: {i} ladder markets", flush=True)
        pd.concat(parts, ignore_index=True).to_parquet(folder / "trades.parquet", index=False)
        print(f"{asset}: {len(p)} pairs, {p.t15.nunique()} hours", flush=True)


def fee(p):
    return FEE * p * (1 - p)


def analyse(window):
    for asset in ("BTC", "ETH"):
        folder = OUT / f"KX{asset}D"
        p = pd.read_parquet(folder / "pairs.parquet")
        lt = pd.read_parquet(folder / "trades.parquet")
        lt["ts"] = pd.to_datetime(lt.created_time, utc=True, format="ISO8601")
        ft = pd.read_parquet(kx.RAW / f"KX{asset}15M" / "trades.parquet")
        ft = ft[ft.ticker.isin(set(p.t15))]
        ft["ts"] = pd.to_datetime(ft.created_time, utc=True, format="ISO8601")
        hits = []
        for (t15, k_star), g in p.groupby(["t15", "k_star"]):
            f15 = ft[ft.ticker == t15].sort_values("ts")
            for _, x in g.iterrows():
                lad = lt[lt.ticker == x.ticker].sort_values("ts")
                if lad.empty or f15.empty:
                    continue
                if x.strike < k_star:  # 15m bid (taker sold YES) vs ladder ask (taker bought YES)
                    bid, ask = f15[f15.taker_side == "no"], lad[lad.taker_side == "yes"]
                else:  # ladder bid vs 15m ask
                    bid, ask = lad[lad.taker_side == "no"], f15[f15.taker_side == "yes"]
                if bid.empty or ask.empty:
                    continue
                m = pd.merge_asof(
                    bid[["ts", "yes_price", "count"]].sort_values("ts"),
                    ask[["ts", "yes_price", "count"]].sort_values("ts"),
                    on="ts",
                    direction="nearest",
                    tolerance=pd.Timedelta(seconds=window),
                    suffixes=("_bid", "_ask"),
                ).dropna()
                m = m[m.yes_price_bid > m.yes_price_ask]
                if len(m):
                    gross = m.yes_price_bid - m.yes_price_ask
                    net = gross - fee(m.yes_price_ask) - fee(1 - m.yes_price_bid)
                    hits.append(
                        m.assign(
                            t15=t15,
                            ladder=x.ticker,
                            gross=gross,
                            net=net,
                            size=np.minimum(m.count_bid, m.count_ask),
                        )
                    )
        n_hours = p.t15.nunique()
        if not hits:
            print(f"{asset}: {n_hours} hours, no crossed print pairs within {window}s")
            continue
        h = pd.concat(hits)
        pos = h[h.net > 0]
        print(
            f"{asset}: {n_hours} hours; crossed print pairs {len(h)} in {h.t15.nunique()} hours; "
            f"net > 0: {len(pos)} in {pos.t15.nunique()} hours, mean net {pos.net.mean():.3f}, "
            f"size-weighted net $ {(pos.net * pos['size']).sum():,.2f}"
        )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["fetch", "analyse"])
    ap.add_argument("--window", type=float, default=2)
    a = ap.parse_args()
    fetch() if a.mode == "fetch" else analyse(a.window)


if __name__ == "__main__":
    main()
