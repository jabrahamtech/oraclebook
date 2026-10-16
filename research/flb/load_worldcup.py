"""Build horizon observations from Kaggle madrading/polymarket-2026-fifa-world-cup-order-book
(every Polymarket market attached to the 2026 FIFA World Cup; 1-minute bars of the first outcome's book
with closing best bid / best ask, printed only in minutes with quote events; official resolutions).
Usage: python -I load_worldcup.py <root> <out.parquet> <exclusions.json>
"""
import json
import pathlib
import sys

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

root, out, excl_out = sys.argv[1:4]
HORIZONS = {"7d": pd.Timedelta(days=7), "1d": pd.Timedelta(days=1), "1h": pd.Timedelta(hours=1)}
MAX_STALE = pd.Timedelta(hours=6)
MAX_SPREAD = 0.05

o = pd.read_csv(f"{root}/outcomes.csv")
rm = pd.read_csv(f"{root}/reference_markets.csv", dtype={"yes_token_id": str, "no_token_id": str})
ex = {"markets": len(o)}
labels = o.outcome_labels.map(lambda s: json.loads(s) if isinstance(s, str) else [])
prices = o.outcome_prices.map(lambda s: sorted(float(v) for v in json.loads(s)) if isinstance(s, str) else [])
ok = o.is_resolved & o.winning_index.isin([0, 1]) & (labels.str.len() == 2) & prices.map(lambda p: p == [0.0, 1.0])
ex["unresolved_void_or_nonbinary"] = int((~ok).sum())
o = o[ok].assign(first_label=labels[ok].str[0])
# yes_token_id is the first listed outcome's token (tokens are labelled YES/NO generically); the
# final-quote check below confirms the mapping against the official result.
o = o.merge(rm[["market_id", "yes_token_id"]], on="market_id", how="inner")
o["y"] = (o.winning_index == 0).astype(int)
o["end"] = pd.to_datetime(o.end_date, utc=True)
o["closed"] = pd.to_datetime(o.closed_time, utc=True, format="mixed")
ex["markets_eligible"] = len(o)

# Horizons are measured back from the scheduled end_date; a market enters only if it was still open then.
targets = pd.concat([o[["market_id", "yes_token_id", "end", "closed"]].assign(horizon=h, t=o.end - d)
                     for h, d in HORIZONS.items()], ignore_index=True)
targets = targets[targets.t < targets.closed]
final = o[["market_id", "yes_token_id", "end", "closed"]].assign(horizon="final_check",
                                                                  t=o.closed - pd.Timedelta(minutes=10))
targets = pd.concat([targets, final], ignore_index=True).rename(columns={"yes_token_id": "token_id"})
targets["t"] = targets.t.astype("datetime64[us, UTC]")
tokens = set(targets.token_id)
pf = pq.ParquetFile(f"{root}/candles_1m_mid.parquet")
cands = []
for i in range(pf.num_row_groups):
    c = pf.read_row_group(i, columns=["token_id", "ts", "bid_close", "ask_close", "source"]).to_pandas()
    c = c[c.token_id.isin(tokens)]
    if c.empty:
        continue
    # A bar's close quote is the book at the end of its minute.
    c = c.assign(t_obs=(c.ts + pd.Timedelta(minutes=1)).astype("datetime64[us, UTC]")).sort_values("t_obs")
    tg = targets[targets.token_id.isin(set(c.token_id))].sort_values("t")
    cands.append(pd.merge_asof(tg, c, left_on="t", right_on="t_obs", by="token_id", direction="backward",
                               tolerance=MAX_STALE).dropna(subset=["t_obs"]))
snap = pd.concat(cands).sort_values("t_obs").groupby(["market_id", "horizon"]).tail(1)
fc = snap[snap.horizon == "final_check"].merge(o[["market_id", "y"]], on="market_id")
fc = fc[(fc.bid_close > 0) & (fc.ask_close < 1)]
ex["final_check_markets"] = len(fc)
ex["final_check_first_token_mid_agrees_with_result"] = float(
    (((fc.bid_close + fc.ask_close) / 2 > 0.5) == (fc.y == 1)).mean())
snap, targets = snap[snap.horizon != "final_check"], targets[targets.horizon != "final_check"]
ex["targets_open_at_sample_time"] = len(targets)
ex["targets_with_quote_within_6h"] = len(snap)
liquid = (snap.bid_close > 0) & (snap.ask_close < 1) & (snap.ask_close - snap.bid_close <= MAX_SPREAD) & (
    snap.bid_close <= snap.ask_close)
ex["dropped_one_sided_or_spread_gt_5c"] = int((~liquid).sum())
snap = snap[liquid].merge(o[["market_id", "event_id", "question", "first_label", "y"]], on="market_id")
rows = []
for s in snap.itertuples(index=False):
    mid = (s.bid_close + s.ask_close) / 2
    base = {"dataset": "polymarket_worldcup", "horizon": s.horizon, "market_id": str(s.market_id),
            "event_id": str(s.event_id), "label": s.first_label, "source": s.source,
            "bid": s.bid_close, "ask": s.ask_close, "stale_min": (s.t - s.t_obs) / pd.Timedelta(minutes=1)}
    rows.append({**base, "side": "first", "p_buy": s.ask_close, "p_cal": mid, "y": s.y})
    rows.append({**base, "side": "second", "p_buy": 1 - s.bid_close, "p_cal": 1 - mid, "y": 1 - s.y})
obs = pd.DataFrame(rows)
ex["markets_used"] = int(obs.market_id.nunique())
ex["events_used"] = int(obs.event_id.nunique())
ex["end_date_range"] = [str(o.end.min()), str(o.end.max())]
obs.to_parquet(out)
pathlib.Path(excl_out).write_text(json.dumps(ex, indent=1))
print(json.dumps(ex, indent=1))
print(obs.groupby(["horizon", "side"]).size())
print(obs.assign(spread=obs.ask - obs.bid).groupby("horizon")[["spread", "stale_min"]].describe().T)
print(np.round(obs[obs.side == "first"].groupby("horizon").y.mean(), 3))
