"""Build horizon observations from J0shusmc/Kalshi-BTC raw KXBTC15M data (1-minute candles with
YES bid/ask). Usage: python -I load_kalshi.py <raw_dir> <out.parquet> <exclusions.json>
"""
import gzip
import json
import pathlib
import sys

import pandas as pd

raw, out, excl_out = sys.argv[1:4]
HORIZONS = {"14m": 14, "10m": 10, "5m": 5, "2m": 2, "1m": 1}


def q(d, k):
    v = d.get(k, d.get(f"{k}_dollars"))
    return None if v is None else float(v)


markets = {}
with gzip.open(f"{raw}/live_markets.jsonl.gz", "rt") as f:
    for line in f:
        for mk in json.loads(line)["response"]["markets"]:
            markets[mk["ticker"]] = mk

ex = {"markets_listed": len(markets)}
rows = []
n_no_candles = 0
n_bad_result = 0
with gzip.open(f"{raw}/candlesticks.jsonl.gz", "rt") as f:
    for line in f:
        d = json.loads(line)
        ticker = d["endpoint"].split("/")[-2]
        mk = markets.get(ticker)
        if mk is None:
            continue
        if mk["result"] not in ("yes", "no"):
            n_bad_result += 1
            continue
        cs = d["response"]["candlesticks"]
        if not cs:
            n_no_candles += 1
            continue
        close_ts = int(pd.Timestamp(mk["close_time"]).timestamp())
        by_end = {c["end_period_ts"]: c for c in cs}
        y = 1 if mk["result"] == "yes" else 0
        day = mk["close_time"][:10]
        for hname, h in HORIZONS.items():
            c = by_end.get(close_ts - 60 * h)
            if c is None:
                continue
            bid, ask = q(c["yes_bid"], "close"), q(c["yes_ask"], "close")
            mid = (bid + ask) / 2 if (bid is not None and ask is not None and 0 < bid and ask < 1) else None
            base = {"dataset": "kalshi_btc15m", "horizon": hname, "market_id": ticker, "event_id": day,
                        "close_ts": close_ts, "bid": bid, "ask": ask}
            if ask is not None and 0 < ask < 1:
                rows.append({**base, "side": "YES", "p_buy": ask, "p_cal": mid, "y": y})
            if bid is not None and 0 < bid < 1:
                rows.append({**base, "side": "NO", "p_buy": 1 - bid,
                             "p_cal": None if mid is None else 1 - mid, "y": 1 - y})
obs = pd.DataFrame(rows)
ex.update(no_candles=n_no_candles, non_yes_no_result=n_bad_result,
          markets_used=int(obs.market_id.nunique()),
          close_time_range=[min(m["close_time"] for m in markets.values()),
                            max(m["close_time"] for m in markets.values())],
          yes_rate=float(pd.Series([m["result"] == "yes" for m in markets.values()]).mean()))
obs.to_parquet(out)
pathlib.Path(excl_out).write_text(json.dumps(ex, indent=1))
print(json.dumps(ex, indent=1))
print(obs.groupby(["horizon", "side"]).size())
print(obs.assign(spread=obs.ask - obs.bid).groupby("horizon").spread.describe())
