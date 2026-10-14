"""Try a sampling methodology on one panel from the command line: sample quotes at anchor + offset,
turn them into contracts, and print favourite / longshot returns per $1 (bootstrap resampling the
chosen cluster) and the calibration slope for each horizon. Descriptive only.

Usage: python -I scan.py <panel_root> <name> --offsets=-1h,-1D [options]
  --anchor COL     markets column to measure from (default close_ts, the scheduled close)
  --before COL     only sample while the market is still open, e.g. resolved_ts
  --stale TD       max quote age (default 1h); --spread X  max bid-ask spread (default none)
  --cluster COL    bootstrap cluster: market_id (default) or event_id
  --parts a,b      quotes parts to read (e.g. btc,eth); --fee R  Polymarket-style fee R*p*(1-p)
Offsets and ages are pandas Timedeltas: -30s, -5min, -1h, -7D.
"""

import argparse
import pathlib
import sys

import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "flb")]
import flb_lib as L
import panels as P

ap = argparse.ArgumentParser(usage=__doc__)
ap.add_argument("panel_root")
ap.add_argument("name")
ap.add_argument("--offsets", required=True)
ap.add_argument("--anchor", default="close_ts")
ap.add_argument("--before")
ap.add_argument("--stale", default="1h")
ap.add_argument("--spread", type=float)
ap.add_argument("--cluster", default="market_id")
ap.add_argument("--parts")
ap.add_argument("--fee", type=float, default=0.0)
ap.add_argument("--boot", type=int, default=2000)
a = ap.parse_args()

m = P.load_markets(a.panel_root, a.name)
offsets = {o: pd.Timedelta(o) for o in a.offsets.split(",")}
quotes = P.quote_parts(a.panel_root, a.name, parts=a.parts.split(",") if a.parts else None)
snap = P.sample(m, quotes, a.anchor, offsets, pd.Timedelta(a.stale), before=a.before)
c = P.contracts(snap, max_spread=a.spread)
n_unknown = int(c.y.isna().sum())
c = c.dropna(subset=["y"]).assign(cluster_id=lambda d: d[a.cluster].astype(str))
print(
    f"{a.name}: {len(m):,} markets; anchor={a.anchor} before={a.before} stale={a.stale} "
    f"spread={a.spread} cluster={a.cluster} fee={a.fee}; "
    f"dropped {n_unknown:,} contracts with unknown y"
)
rng = np.random.default_rng(L.SEED)
fees = {"fee": lambda p: L.poly_fee(p, a.fee)} if a.fee else {}
for h in offsets:
    o = c[c.horizon == h]
    if o.empty:
        print(f"\n{h}: no contracts")
        continue
    t = L.bin_table(o, L.HEADLINE, fees, rng)
    cols = [
        "bin",
        "n_contracts",
        "n_markets",
        "n_clusters",
        "mean_price",
        "win_rate",
        "ret_ew",
        "ret_ew_lo",
        "ret_ew_hi",
    ] + (["ret_ew_fee", "ret_ew_fee_lo", "ret_ew_fee_hi"] if fees else [])
    cal = o[o.token == 0].dropna(subset=["p_cal"])
    slope = (
        L.calib_slope(cal.p_cal, cal.y, cal.cluster_id, rng, b=min(a.boot, 1000))
        if len(cal) > 50
        else None
    )
    print(f"\n{h}: {len(o):,} contracts, {o.market_id.nunique():,} markets")
    print(t[[x for x in cols if x in t]].to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    if slope:
        print(f"calibration slope (first token, p_cal) {L.fmt_ci(slope[0], *slope[1], pct=False)}")
