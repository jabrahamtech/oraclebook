"""Build horizon observations from Kaggle kachoio/polymarket-5-minute-crypto-updown-markets
(Polymarket 5-minute crypto Up/Down markets, per-second best bid/ask for the Up and Down books).
`outcome` is inferred by the publisher from the final tick and is null when that tick was not decisive
(a close call); those markets are kept with y = NaN so run_flb.py can bound their effect.
Usage: python -I load_kachoio.py <root> <out.parquet> <exclusions.json>
"""
import glob
import json
import os
import pathlib
import sys

import pandas as pd

root, out, excl_out = sys.argv[1:4]
HORIZONS = {"4m": 240, "2m": 120, "1m": 60, "30s": 30}
MAX_STALE_S = 5
MAX_SPREAD = 0.05

rows = []
ex = {}
for mf in sorted(glob.glob(f"{root}/*_markets.parquet")):
    coin = os.path.basename(mf).split("_")[0]
    m = pd.read_parquet(mf)
    e = {"markets": len(m)}
    e["outcome_null_kept_for_bounds"] = int((~m.outcome.isin(["Up", "Down"])).sum())
    m = m.copy()
    # Horizons are measured back from the scheduled window end, which is fixed when the market is listed.
    m["end_s"] = (m.market_end - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1)
    targets = pd.concat([m[["condition_id", "end_s"]].assign(horizon=h, t=m.end_s - s)
                         for h, s in HORIZONS.items()], ignore_index=True)
    ticks = pd.read_parquet(f"{root}/{coin}_ticks.parquet", columns=["condition_id", "t", "bu", "au", "bd", "ad"])
    ticks = ticks[ticks.condition_id.isin(m.condition_id)]
    snap = pd.merge_asof(targets.sort_values("t"), ticks.sort_values("t").rename(columns={"t": "t_obs"}),
                         left_on="t", right_on="t_obs", by="condition_id", direction="backward",
                         tolerance=MAX_STALE_S)
    e["missing_snapshot"] = int(snap.t_obs.isna().sum())
    snap = snap.dropna(subset=["t_obs"]).merge(
        m[["condition_id", "outcome", "market_end"]], on="condition_id")
    for s in snap.itertuples(index=False):
        y_up = {"Up": 1, "Down": 0}.get(s.outcome, float("nan"))
        if s.bu > s.au:
            continue
        mid = (s.bu + s.au) / 2 if (0 < s.bu and s.au < 1) else None
        base = {"dataset": "polymarket_kachoio_5m", "coin": coin, "horizon": s.horizon,
                "market_id": s.condition_id, "event_id": s.market_end.strftime("%Y-%m-%d"),
                "close_ts": int(s.end_s), "bid": s.bu, "ask": s.au}
        if 0 < s.au < 1 and s.au - s.bu <= MAX_SPREAD:
            rows.append({**base, "side": "Up", "p_buy": s.au, "p_cal": mid, "y": y_up})
        if 0 < s.ad < 1 and s.ad - s.bd <= MAX_SPREAD:
            rows.append({**base, "side": "Down", "p_buy": s.ad, "p_cal": None if mid is None else 1 - mid,
                         "y": 1 - y_up})
    e["close_range"] = [str(m.market_end.min()), str(m.market_end.max())]
    ex[coin] = e
obs = pd.DataFrame(rows)
ex["markets_used"] = int(obs.market_id.nunique())
ex["markets_used_with_outcome"] = int(obs.dropna(subset=["y"]).market_id.nunique())
ex["up_rate"] = float(obs[(obs.side == "Up")].drop_duplicates("market_id").y.mean())
obs.to_parquet(out)
pathlib.Path(excl_out).write_text(json.dumps(ex, indent=1))
print(json.dumps(ex, indent=1))
print(obs.groupby(["horizon", "side"]).size())
print(obs.assign(spread=obs.ask - obs.bid).groupby("horizon").spread.describe())
