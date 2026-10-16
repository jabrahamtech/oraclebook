"""Build horizon observations from Kaggle polyorderbooks/polymarket-crypto-orderbooks-1s-l2
(Polymarket crypto Up/Down markets, 5m / 15m / 4h contracts, ~1-second L2 snapshots per outcome token).
Usage: python -I load_polyorderbooks.py <root> <out.parquet> <exclusions.json>
"""
import json
import pathlib
import sys

import pandas as pd

root, out, excl_out = sys.argv[1:4]
HORIZONS = {
    "5m": {"4m": 240, "2m": 120, "1m": 60, "30s": 30},
    "15m": {"14m": 840, "10m": 600, "5m": 300, "1m": 60},
    "4h": {"3h": 10800, "1h": 3600, "30m": 1800, "5m": 300},
}
MAX_STALE_S = 15
MAX_SPREAD = 0.05

rows = []
ex = {}
for length, hz in HORIZONS.items():
    x = pd.read_parquet(f"{root}/updown_{length}.parquet",
                        columns=["market_slug", "coin", "outcome", "is_winning_outcome", "market_end_at",
                                 "captured_at", "best_bid", "best_ask", "crossed"])
    e = {"markets": int(x.market_slug.nunique()), "snapshots": len(x),
         "crossed_or_one_sided_dropped": int((x.crossed | x.best_bid.isna() | x.best_ask.isna()).sum())}
    x = x[~x.crossed & x.best_bid.notna() & x.best_ask.notna()].copy()
    last = (x[x.best_ask - x.best_bid <= MAX_SPREAD].sort_values("captured_at")
            .groupby(["market_slug", "outcome"]).tail(1))
    e["last_tight_mid_agrees_with_is_winning_outcome"] = float(
        (((last.best_bid + last.best_ask) / 2 > 0.5) == last.is_winning_outcome).mean())
    x["t_obs"] = (x.captured_at - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1)
    # Horizons are measured back from the scheduled window end (market_end_at), not from resolution.
    mk = x.drop_duplicates(["market_slug", "outcome"])[["market_slug", "coin", "outcome", "is_winning_outcome",
                                                        "market_end_at"]]
    mk = mk.assign(end_s=(mk.market_end_at - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1))
    targets = pd.concat([mk.assign(horizon=f"{length}:{h}", t=mk.end_s - s) for h, s in hz.items()],
                        ignore_index=True)
    snap = pd.merge_asof(targets.sort_values("t"),
                         x[["market_slug", "outcome", "t_obs", "best_bid", "best_ask"]].sort_values("t_obs"),
                         left_on="t", right_on="t_obs", by=["market_slug", "outcome"], direction="backward",
                         tolerance=MAX_STALE_S)
    e["missing_snapshot"] = int(snap.t_obs.isna().sum())
    snap = snap.dropna(subset=["t_obs"])
    for s in snap.itertuples(index=False):
        if not (0 < s.best_ask < 1 and s.best_ask - s.best_bid <= MAX_SPREAD):
            continue
        mid = (s.best_bid + s.best_ask) / 2 if s.best_bid > 0 else None
        rows.append({"dataset": "polymarket_polyorderbooks", "length": length, "coin": s.coin,
                     "horizon": s.horizon, "market_id": s.market_slug,
                     "event_id": s.market_end_at.strftime("%Y-%m-%d %H"), "close_ts": int(s.end_s),
                     "side": s.outcome, "bid": s.best_bid, "ask": s.best_ask, "p_buy": s.best_ask, "p_cal": mid,
                     "y": int(s.is_winning_outcome)})
    e["close_range"] = [str(mk.market_end_at.min()), str(mk.market_end_at.max())]
    ex[length] = e
obs = pd.DataFrame(rows)
ex["markets_used"] = int(obs.market_id.nunique())
obs.to_parquet(out)
pathlib.Path(excl_out).write_text(json.dumps(ex, indent=1))
print(json.dumps(ex, indent=1))
print(obs.groupby(["horizon", "side"]).size())
print(obs.assign(spread=obs.ask - obs.bid).groupby("horizon").spread.describe())
