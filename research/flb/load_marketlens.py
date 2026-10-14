"""Build horizon observations from marketlenstrade/polymarket-historical-data snapshots.csv
(Polymarket top-of-book; quotes are for the first-listed outcome token).
Usage: python -I load_marketlens.py <root> <out.parquet> <exclusions.json>
"""
import glob
import json
import os
import pathlib
import sys

import numpy as np
import pandas as pd

root, out, excl_out = sys.argv[1:4]
OFFSETS = {"open+5m": 5, "open+30m": 30, "open+90m": 90}
MAX_STALE_MIN = 15
MAX_SPREAD = 0.05

rows = []
ex = {}
for d in sorted(glob.glob(f"{root}/*/")):
    if not os.path.exists(d + "markets.csv"):
        continue
    name = os.path.basename(d[:-1])
    m = pd.read_csv(d + "markets.csv")
    s = pd.read_csv(d + "snapshots.csv").sort_values("t_ms")
    outs = m.outcomes.str.split("|")
    ok2 = outs.str.len() == 2
    first, second = outs.str[0], outs.str[1]
    valid = ok2 & (m.winning_outcome.eq(first) | m.winning_outcome.eq(second))
    ex[name] = {"markets": len(m), "excluded_void_or_nonbinary": int((~valid).sum())}
    m = m[valid].copy()
    m["y"] = (m.winning_outcome == first[valid]).astype(int)
    m["res_ms"] = (pd.to_datetime(m.resolved_at, utc=True) - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(milliseconds=1)
    m["start_ms"] = (pd.to_datetime(m.data_start, utc=True) - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(milliseconds=1)
    sg = {k: g for k, g in s.groupby("market_id")}
    used = set()
    for mk in m.itertuples(index=False):
        g = sg.get(mk.market_id)
        if g is None:
            continue
        t = g.t_ms.to_numpy()
        for hname, h in OFFSETS.items():
            target = mk.start_ms + h * 60_000
            if target >= mk.res_ms:
                continue
            i = np.searchsorted(t, target, side="right") - 1
            if i < 0 or (target - t[i]) > MAX_STALE_MIN * 60_000:
                continue
            bid, ask = g.best_bid.iloc[i], g.best_ask.iloc[i]
            if not (pd.notna(bid) and pd.notna(ask) and 0 < bid and ask < 1 and ask - bid <= MAX_SPREAD):
                continue
            mid = (bid + ask) / 2 if (pd.notna(bid) and pd.notna(ask) and bid > 0 and ask < 1) else np.nan
            base = {"dataset": "polymarket_marketlens_book", "folder": name, "horizon": hname,
                        "market_id": mk.market_id, "event_id": f"{name}|{mk.event}", "subtype": mk.subtype,
                        "bid": bid, "ask": ask, "volume_usd": mk.volume_usd}
            if pd.notna(ask) and 0 < ask < 1:
                rows.append({**base, "side": "first", "p_buy": ask, "p_cal": mid, "y": mk.y})
                used.add(mk.market_id)
            if pd.notna(bid) and 0 < bid < 1:
                rows.append({**base, "side": "second", "p_buy": 1 - bid, "p_cal": 1 - mid, "y": 1 - mk.y})
                used.add(mk.market_id)
    ex[name]["markets_used"] = len(used)
obs = pd.DataFrame(rows)
obs.to_parquet(out)
pathlib.Path(excl_out).write_text(json.dumps(ex, indent=1))
print(json.dumps(ex, indent=1))
print(obs.groupby(["horizon", "side"]).size())
print(obs.assign(spread=obs.ask - obs.bid).groupby("horizon").spread.describe())
