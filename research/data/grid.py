"""Run a grid of sampling methodologies over every panel, one OS process per (dataset, anchor) job,
and write one tidy table. Descriptive only: nothing here shows that any strategy is profitable.

Variants per job (sampling is done once per job, variants reuse it):
  anchor  scheduled close (or listing/open for marketlens) vs actual resolution, which leaks the
          outcome when a market resolves early and is included to show that bias
  spread  no cap, <= 5c, <= 2c (book datasets)
  price   ask (pay the spread) vs mid (no spread cost; a reference, not an achievable fill)
Each row is one (job, variant, horizon, bin) with gross and fee-net returns per $1, CIs from
resampling markets and from resampling events, and the first-token calibration slope.

Usage: python -I grid.py <panel_root> <out_dir> [--jobs N] [--boot B] [name ...]
"""

import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "flb"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import flb_lib as L
import panels as P

TD = pd.Timedelta
SPREADS = (None, 0.05, 0.02)
CLOSE = "close_ts"


def j(name, anchor, offsets, stale, before=None, parts=None, label=None, prefilter=None, fee="pm7"):
    return {
        "name": name,
        "anchor": anchor,
        "offsets": offsets,
        "stale": stale,
        "before": before,
        "parts": parts,
        "label": label or anchor,
        "prefilter": prefilter,
        "fee": fee,
        "book": True,
    }


def offs(*xs):
    return {x: TD(x) for x in xs}


def jobs():
    out = []
    for a, b in ((CLOSE, "resolved_ts"), ("resolved_ts", None)):
        out.append(j("pancake", a, offs("-30D", "-7D", "-1D"), TD("2.5D"), b) | {"book": False})
        out.append(
            # 1-minute candles: < 60s keeps only the candle ending at (or just before) the sample.
            j(
                "kalshi",
                a,
                offs("-14min", "-10min", "-5min", "-2min", "-1min"),
                TD("59s"),
                b,
                fee="kx",
            )
        )
        out.append(j("worldcup", a, offs("-7D", "-1D", "-1h"), TD("6h"), b))
        out.append(j("marketlens", a, offs("-1D", "-6h", "-1h"), TD("15min"), b))
    out.append(
        j("marketlens", "data_start_ts", offs("5min", "30min", "90min"), TD("15min"), "resolved_ts")
    )
    out.append(j("kachoio", CLOSE, offs("-240s", "-120s", "-60s", "-30s"), TD("5s")))
    for length, h in {
        "5m": ("-240s", "-120s", "-60s", "-30s"),
        "15m": ("-14min", "-10min", "-5min", "-1min"),
        "4h": ("-3h", "-1h", "-30min", "-5min"),
    }.items():
        out.append(
            j(
                "polyorderbooks",
                CLOSE,
                offs(*h),
                TD("15s"),
                parts=[length],
                label=f"{CLOSE}|{length}",
                prefilter="two_sided_uncrossed",
            )
        )
    # Split each job per horizon and spread cap so the large datasets spread over every core.
    return [
        x | {"offsets": {h: d}, "spreads": (sp,)}
        for x in out
        for h, d in x["offsets"].items()
        for sp in (SPREADS if x["book"] else (None,))
    ]


FEES = {"pm7": lambda p: L.poly_fee(p, 0.07), "kx": L.kalshi_fee}


def run_job(job, panel_root, boot):
    t0 = time.time()
    rng = np.random.default_rng(L.SEED)
    m = P.load_markets(panel_root, job["name"])
    if job["name"] == "polyorderbooks":
        m = m[m.length == job["parts"][0]]
    quotes = P.quote_parts(panel_root, job["name"], parts=job["parts"])
    if job["prefilter"]:
        quotes = (q[q.bid.notna() & q.ask.notna() & (q.bid < q.ask)] for q in quotes)
    snap = P.sample(m, quotes, job["anchor"], job["offsets"], job["stale"], before=job["before"])
    if snap.empty:
        return [], {"job": job["label"], "name": job["name"], "seconds": time.time() - t0}
    price_only = snap.ask.isna().all() if "ask" in snap else True
    fee = {"fee": FEES[job["fee"]]}
    rows = []
    for spread in job["spreads"]:
        c = P.contracts(snap, max_spread=spread).dropna(subset=["y"])
        for price in ("ask",) if price_only else ("ask", "mid"):
            cc = c if price == "ask" else c.dropna(subset=["p_cal"]).assign(p_buy=lambda d: d.p_cal)
            for h in job["offsets"]:
                o = cc[cc.horizon == h]
                if o.empty:
                    continue
                cal = o[o.token == 0].dropna(subset=["p_cal"])
                slope = (
                    L.calib_slope(cal.p_cal, cal.y, cal.market_id, rng, b=boot)
                    if len(cal) > 50 and cal.y.nunique() == 2
                    else None
                )
                tm = L.bin_table(o.assign(cluster_id=o.market_id), L.HEADLINE, fee, rng)
                te = L.bin_table(o.assign(cluster_id=o.event_id), L.HEADLINE, {}, rng)
                for (_, a), (_, e) in zip(tm.iterrows(), te.iterrows(), strict=True):
                    rows.append(
                        {
                            "dataset": job["name"],
                            "anchor": job["label"],
                            "before": job["before"],
                            "stale": str(job["stale"]),
                            "spread_cap": spread,
                            "price": price,
                            "fee_model": job["fee"],
                            "horizon": h,
                            "bin": a["bin"],
                            "n_contracts": a.get("n_contracts", 0),
                            "n_markets": a.get("n_markets"),
                            "n_events": e.get("n_clusters"),
                            "mean_price": a.get("mean_price"),
                            "win_rate": a.get("win_rate"),
                            "ret": a.get("ret_ew"),
                            "ret_lo_mkt": a.get("ret_ew_lo"),
                            "ret_hi_mkt": a.get("ret_ew_hi"),
                            "ret_lo_evt": e.get("ret_ew_lo"),
                            "ret_hi_evt": e.get("ret_ew_hi"),
                            "ret_net_fee": a.get("ret_ew_fee"),
                            "ret_net_lo_mkt": a.get("ret_ew_fee_lo"),
                            "ret_net_hi_mkt": a.get("ret_ew_fee_hi"),
                            "slope": None if slope is None else slope[0],
                            "slope_lo": None if slope is None else slope[1][0],
                            "slope_hi": None if slope is None else slope[1][1],
                            "n_cal": len(cal),
                        }
                    )
    return rows, {
        "job": job["label"],
        "name": job["name"],
        "seconds": time.time() - t0,
        "rows": len(rows),
    }


def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("panel_root")
    ap.add_argument("out_dir")
    ap.add_argument("names", nargs="*")
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--one", type=int, help=argparse.SUPPRESS)
    a = ap.parse_args()
    out = pathlib.Path(a.out_dir)
    parts = out / "parts"
    todo = jobs()
    if a.one is not None:
        # Tiny cells make NaN bootstrap draws; those cells report NaN CIs.
        warnings.filterwarnings("ignore", category=RuntimeWarning)
        rows, info = run_job(todo[a.one], a.panel_root, a.boot)
        pd.DataFrame(rows).to_pickle(parts / f"{a.one:02d}.pkl")
        print(
            f"done {info['name']:15s} {info['job']:22s} {info['seconds']:6.1f}s "
            f"{info.get('rows', 0)} rows",
            flush=True,
        )
        return
    idx = [i for i, x in enumerate(todo) if not a.names or x["name"] in a.names]
    # Biggest first so the long jobs start immediately.
    size = {"kachoio": 0, "worldcup": 1, "pancake": 2}
    idx.sort(key=lambda i: size.get(todo[i]["name"], 9))
    parts.mkdir(parents=True, exist_ok=True)
    for f in parts.glob("*.pkl"):
        f.unlink()
    # One OS process per job (threads only wait on them), so every core is used.
    cmd = [sys.executable, "-I", __file__, a.panel_root, a.out_dir, "--boot", str(a.boot), "--one"]
    with ThreadPoolExecutor(max_workers=min(a.jobs, len(idx))) as ex:
        codes = list(ex.map(lambda i: subprocess.run([*cmd, str(i)]).returncode, idx))
    if any(codes):
        sys.exit(f"{sum(c != 0 for c in codes)} job(s) failed")
    g = pd.concat([pd.read_pickle(parts / f"{i:02d}.pkl") for i in idx], ignore_index=True)
    g = g.sort_values(
        ["dataset", "anchor", "spread_cap", "price", "horizon", "bin"], na_position="first"
    )
    g.to_csv(out / "grid.csv", index=False)
    shutil.rmtree(parts)
    print(f"wrote {out / 'grid.csv'}: {len(g)} rows from {len(idx)} jobs")


if __name__ == "__main__":
    main()
