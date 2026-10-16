"""Build horizon observations from usepancake/polymarket-history (Polymarket daily bars).

Usage: python -I load_pancake.py <repo_root> <out.parquet> <exclusions.json>
"""
import glob
import json
import pathlib
import sys

import numpy as np
import pandas as pd

root, out, excl_out = sys.argv[1:4]
DAY = 86400
HORIZONS = {"30d": 30 * DAY, "7d": 7 * DAY, "1d": 1 * DAY}
MAX_STALE = 2.5 * DAY


def load(t, cols=None):
    fs = sorted(glob.glob(f"{root}/datasets/v1/polymarket/{t}/*/*/*.parquet"))
    return pd.concat([pd.read_parquet(f, columns=cols) for f in fs], ignore_index=True)


m = load("markets", ["market_id", "title", "category", "market_kind", "status", "resolves_at_planned",
                     "total_volume_usd"])
o = load("outcomes", ["instrument_id", "market_id", "label", "payoff_on_resolve", "sort_order"])
r = load("resolutions", ["market_id", "resolved_at", "winning_outcome_instrument_id"])
c = load("candles_1d", ["instrument_id", "market_id", "observed_at", "released_at", "close"])

ex = {"markets_total": len(m), "resolved_markets": int(r.market_id.nunique())}
res = r.merge(m, on="market_id", how="left")
res = res[res.market_kind == "binary"]
oc = o.groupby("market_id").size()
res = res[res.market_id.map(oc) == 2]
ex["resolved_binary_2leg"] = len(res)

legs = o[o.market_id.isin(res.market_id)].copy()
win = res.set_index("market_id").winning_outcome_instrument_id
legs["y_res"] = (legs.instrument_id == legs.market_id.map(win)).astype(int)
mismatch = (legs.y_res != legs.payoff_on_resolve.round().astype(int)).groupby(legs.market_id).any()
ex["payoff_vs_resolution_mismatch"] = int(mismatch.sum())
bad_payoff = legs.groupby("market_id").payoff_on_resolve.apply(lambda s: sorted(s.round(6)) != [0.0, 1.0])
ex["non_0_1_payoff_(void/50-50)"] = int(bad_payoff.sum())
drop = set(mismatch[mismatch].index) | set(bad_payoff[bad_payoff].index)
res = res[~res.market_id.isin(drop)]
legs = legs[legs.market_id.isin(res.market_id)]

c = c[c.instrument_id.isin(legs.instrument_id)].sort_values(["instrument_id", "released_at"])
has_c = set(c.market_id)
ex["no_candles"] = int((~res.market_id.isin(has_c)).sum())
res = res[res.market_id.isin(has_c)]
nun = c.groupby("market_id").close.nunique()
flat = set(nun[nun <= 1].index)
ex["flat_price_series_(never_moved)"] = int(res.market_id.isin(flat).sum())
res = res[~res.market_id.isin(flat)]

planned = pd.to_datetime(res.resolves_at_planned, errors="coerce", utc=True)
planned_s = (planned - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds()
res["planned_ts"] = planned_s
ex["planned_end_missing"] = int(planned_s.isna().sum())
res["anchor_min"] = np.fmin(res.resolved_at.astype(float), planned_s.fillna(np.inf))
res["anchor_resolved"] = res.resolved_at.astype(float)
res["anchor_planned"] = planned_s
anchor_day = pd.to_datetime(res.anchor_min, unit="s", utc=True).dt.strftime("%Y-%m-%d")
res["event_id"] = res.category.fillna("na") + "|" + anchor_day

legs = legs[legs.market_id.isin(res.market_id)]
rows = []
cg = {k: g for k, g in c.groupby("instrument_id")}
meta = res.set_index("market_id")
for leg in legs.itertuples(index=False):
    g = cg.get(leg.instrument_id)
    if g is None:
        continue
    rel = g.released_at.to_numpy()
    obs_t = g.observed_at.to_numpy()
    px = g.close.to_numpy()
    mrow = meta.loc[leg.market_id]
    for anchor_name in ("anchor_planned", "anchor_min", "anchor_resolved"):
        anchor = mrow[anchor_name]
        if not np.isfinite(anchor):
            continue
        for hname, h in HORIZONS.items():
            target = anchor - h
            if anchor_name == "anchor_planned" and target >= mrow.resolved_at:
                continue
            i = np.searchsorted(rel, target, side="right") - 1
            if i < 0:
                continue
            if target - obs_t[i] > MAX_STALE:
                continue
            rows.append({"dataset": "polymarket_pancake_daily", "anchor": anchor_name, "horizon": hname,
                             "market_id": leg.market_id, "event_id": mrow.event_id, "category": mrow.category,
                             "side": leg.label, "sort_order": leg.sort_order, "p_cal": px[i], "p_buy": px[i],
                             "y": int(leg.y_res), "obs_ts": int(obs_t[i]), "anchor_ts": anchor,
                             "resolved_ts": int(mrow.resolved_at)})
obs = pd.DataFrame(rows)
ex["markets_used_any_horizon"] = int(obs.market_id.nunique())
ex["resolved_at_range"] = [str(pd.to_datetime(res.resolved_at.min(), unit="s")),
                           str(pd.to_datetime(res.resolved_at.max(), unit="s"))]
obs.to_parquet(out)
pathlib.Path(excl_out).write_text(json.dumps(ex, indent=1, default=int))
print(json.dumps(ex, indent=1, default=int))
print(obs.groupby(["anchor", "horizon", "sort_order"]).size())
