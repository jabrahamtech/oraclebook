"""Run favorite-longshot / calibration analysis on the three observation tables and write markdown.
Usage: python -I run_flb.py <out_dir> <results.md>
"""
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import flb_lib as L

out_dir, md_path = sys.argv[1:3]
rng = np.random.default_rng(L.SEED)

FEES = {
    "pm4": lambda p: L.poly_fee(p, 0.04),
    "pm7": lambda p: L.poly_fee(p, 0.07),
    "kx": L.kalshi_fee,
}


def merged_table(obs, bins):
    a = L.bin_table(obs.assign(cluster_id=obs.market_id), bins, FEES, rng)
    b = L.bin_table(obs.assign(cluster_id=obs.event_id), bins, FEES, rng)
    keep_b = ["bin", "n_clusters", "ret_ew_lo", "ret_ew_hi", "ret_cl", "ret_cl_lo", "ret_cl_hi",
              "ret_cl_pm4", "ret_cl_pm7", "ret_cl_kx"]
    b = b[[c for c in keep_b if c in b]].rename(columns={
        "n_clusters": "n_events", "ret_ew_lo": "ret_ew_evlo", "ret_ew_hi": "ret_ew_evhi",
        "ret_cl": "ret_ev", "ret_cl_lo": "ret_ev_lo", "ret_cl_hi": "ret_ev_hi",
        "ret_cl_pm4": "ret_ev_pm4", "ret_cl_pm7": "ret_ev_pm7", "ret_cl_kx": "ret_ev_kx"})
    return a.merge(b, on="bin", how="left")


def pct(v):
    return "–" if v is None or pd.isna(v) else f"{v*100:+.1f}%"


def md_table(t, fee_cols):
    hdr = ("| bin | n contracts | n markets | n events | mean buy price | win rate | "
           "return/$ EW [95% CI, market resample] | EW CI (event resample) | "
           "event-clustered mean [95% CI] | " + " | ".join(f"EW after {f}" for f in fee_cols) + " |")
    lines = [hdr, "|" + "---|" * (9 + len(fee_cols))]
    for r in t.itertuples(index=False):
        r = r._asdict()
        if not r.get("n_contracts"):
            lines.append(f"| {r['bin']} | 0 |" + " |" * (8 + len(fee_cols)))
            continue
        fees = " | ".join(
            f"{pct(r[f'ret_ew_{f}'])} [{pct(r[f'ret_ew_{f}_lo'])}, {pct(r[f'ret_ew_{f}_hi'])}]" for f in fee_cols)
        lines.append(
            f"| {r['bin']} | {int(r['n_contracts'])} | {int(r['n_markets'])} | {int(r['n_events'])} | {r['mean_price']:.3f} | "
            f"{r['win_rate']:.3f} | {pct(r['ret_ew'])} [{pct(r['ret_ew_lo'])}, {pct(r['ret_ew_hi'])}] | "
            f"[{pct(r['ret_ew_evlo'])}, {pct(r['ret_ew_evhi'])}] | "
            f"{pct(r['ret_ev'])} [{pct(r['ret_ev_lo'])}, {pct(r['ret_ev_hi'])}] | {fees} |")
    return "\n".join(lines)


def calib_block(obs_cal, label):
    o = obs_cal.dropna(subset=["p_cal"])
    o = o[(o.p_cal > 0) & (o.p_cal < 1)]
    b, bci, a, aci = L.calib_slope(o.p_cal.to_numpy(), o.y.to_numpy(), o.event_id.to_numpy(), rng)
    _bm, bmci, _, _ = L.calib_slope(o.p_cal.to_numpy(), o.y.to_numpy(), o.market_id.to_numpy(), rng)
    return {"label": label, "n": len(o), "n_markets": o.market_id.nunique(), "n_events": o.event_id.nunique(),
                "brier": L.brier(o.p_cal, o.y), "slope": b, "slope_ci_event": bci, "slope_ci_market": bmci,
                "intercept": a, "intercept_ci": aci, "mean_p": o.p_cal.mean(), "mean_y": o.y.mean()}


def calib_md(rows):
    lines = [("| sample | n | markets | events | mean price | outcome rate | Brier | slope b [95% CI event resample] "
             "| slope CI (market resample) | intercept a [95% CI] |"), "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['label']} | {r['n']} | {r['n_markets']} | {r['n_events']} | {r['mean_p']:.3f} | "
                     f"{r['mean_y']:.3f} | {r['brier']:.4f} | {r['slope']:.3f} [{r['slope_ci_event'][0]:.3f}, "
                     f"{r['slope_ci_event'][1]:.3f}] | [{r['slope_ci_market'][0]:.3f}, {r['slope_ci_market'][1]:.3f}] | "
                     f"{r['intercept']:+.3f} [{r['intercept_ci'][0]:+.3f}, {r['intercept_ci'][1]:+.3f}] |")
    return "\n".join(lines)


sections = []
summary = {}
BINS = L.DECILES + L.FINE

# ---------------- Polymarket (Pancake daily) ----------------
pk = pd.read_parquet(f"{out_dir}/obs_pancake.parquet")
prim = pk[(pk.anchor == "anchor_planned") & (pk.sort_order == 0)]
cal_rows, sec = [], ["## A. Polymarket — usepancake/polymarket-history (daily 00:00 UTC price samples)\n",
                     ("Primary spec: one contract per market (first-listed outcome, i.e. `Yes` for Yes/No markets), "
                     "price = last daily bar released at or before `T − horizon`, where T = the market's planned end "
                     "date (ex-ante), and the market must still be unresolved at `T − horizon`. Buy price = reported "
                     "price (no spread available). Bars staler than 2.5 days at the target time are dropped. "
                     "Rows labelled 'biased anchor' instead measure the horizon back from the realised resolution "
                     "time (or min(resolution, planned end)); that conditions on when the outcome happened and is "
                     "shown only to quantify the look-ahead it introduces.\n")]
for h in ["30d", "7d", "1d"]:
    o = prim[prim.horizon == h]
    cal_rows.append(calib_block(o, f"Polymarket {h}"))
both = pk[(pk.anchor == "anchor_planned")]
for h in ["7d", "1d"]:
    for an, lab in [("anchor_min", "biased anchor = min(resolved, planned)"), ("anchor_resolved", "biased anchor = resolved_at")]:
        cal_rows.append(calib_block(pk[(pk.anchor == an) & (pk.sort_order == 0) & (pk.horizon == h)],
                                    f"Polymarket {h} ({lab})"))
for cat_lab, sel in [("sports", prim.category == "sports"), ("non-sports", prim.category != "sports")]:
    cal_rows.append(calib_block(prim[sel & (prim.horizon == "7d")], f"Polymarket 7d {cat_lab}"))
sec.append("### Calibration (logistic regression of outcome on logit(price); b>1 ⇒ prices too compressed toward "
           "0.5, i.e. favorite-longshot bias; b<1 ⇒ prices too extreme)\n")
sec.append(calib_md(cal_rows))
for h in ["30d", "7d", "1d"]:
    o = prim[prim.horizon == h]
    t = merged_table(o, BINS)
    sec.append(f"\n### Polymarket horizon {h}: bins (deciles, then fine tails)\n")
    sec.append(md_table(t, ["pm4", "pm7"]))
    ht = merged_table(o, L.HEADLINE)
    summary[f"polymarket_{h}"] = ht
    sec.append(f"\nHeadline (longshot p≤0.10, favorite p≥0.90), horizon {h}:\n")
    sec.append(md_table(ht, ["pm4", "pm7"]))
    ht2 = merged_table(both[both.horizon == h], L.HEADLINE)
    sec.append(f"\nRobustness — both legs of every market (each market contributes p and 1−p), {h}:\n")
    sec.append(md_table(ht2, ["pm4", "pm7"]))
    naive = pk[(pk.anchor == "anchor_min") & (pk.sort_order == 0) & (pk.horizon == h)]
    sec.append(f"\nBiased comparison — anchor = min(resolved_at, planned end) (look-ahead via resolution timing), {h}:\n")
    sec.append(md_table(merged_table(naive, BINS), ["pm4", "pm7"]))
    for cat_lab, sel in [("sports", o.category == "sports"), ("non-sports", o.category != "sports")]:
        sec.append(f"\nBy category — {cat_lab}, {h}:\n")
        sec.append(md_table(merged_table(o[sel], L.HEADLINE), ["pm4", "pm7"]))
sections.append("\n".join(sec))

# ---------------- Kalshi BTC15 ----------------
kx = pd.read_parquet(f"{out_dir}/obs_kalshi.parquet")
sec = ["\n## B. Kalshi — KXBTC15M (BTC up/down in 15 min), J0shusmc/Kalshi-BTC raw 1-minute candles\n",
       ("Both sides per market: buy YES at the YES ask, buy NO at 1 − YES bid (minute-close quotes), at "
       "h minutes before close. Calibration uses the YES quote midpoint (one row per market). Event cluster = "
       "UTC calendar day (adjacent 15-min BTC markets share price paths).\n")]
cal_rows = [calib_block(kx[(kx.side == "YES") & (kx.horizon == h)], f"Kalshi BTC15 {h} before close")
            for h in ["14m", "10m", "5m", "2m", "1m"]]
sec.append("### Calibration (YES midpoint)\n")
sec.append(calib_md(cal_rows))
for h in ["10m", "5m", "1m"]:
    o = kx[kx.horizon == h]
    sec.append(f"\n### Kalshi horizon {h}: bins by ask price paid\n")
    sec.append(md_table(merged_table(o, BINS), ["kx", "pm7"]))
    ht = merged_table(o, L.HEADLINE)
    summary[f"kalshi_{h}"] = ht
    sec.append(f"\nHeadline, {h}:\n")
    sec.append(md_table(ht, ["kx", "pm7"]))
sections.append("\n".join(sec))

# ---------------- Marketlens Polymarket books ----------------
ml = pd.read_parquet(f"{out_dir}/obs_marketlens.parquet")
sec = ["\n## C. Polymarket — marketlenstrade/polymarket-historical-data top-of-book snapshots (7 sample events)\n",
       ("Mostly in-play sports/esports plus BTC up/down, weather and AAPL buckets, all within single days. "
       "Price sampled at fixed offsets after recording start (≈ market/game open), only for markets still unresolved then. "
       "Buy first outcome at best ask, second at 1 − best bid; snapshot must be ≤15 min old at target and the book "
       "two-sided with spread ≤ 0.05 (ex-ante liquidity filter). "
       "Event cluster = game/event within folder. Very small number of independent events — treat as anecdotal.\n")]
cal_rows = [calib_block(ml[(ml.side == "first") & (ml.horizon == h)], f"Marketlens {h}")
            for h in ["open+5m", "open+30m", "open+90m"]]
sec.append(calib_md(cal_rows))
for h in ["open+5m", "open+30m", "open+90m"]:
    o = ml[ml.horizon == h]
    ht = merged_table(o, L.HEADLINE)
    summary[f"marketlens_{h}"] = ht
    sec.append(f"\nHeadline, {h}:\n")
    sec.append(md_table(ht, ["pm4", "pm7"]))
    sec.append(f"\nDeciles, {h}:\n")
    sec.append(md_table(merged_table(o, L.DECILES), ["pm4", "pm7"]))
sections.append("\n".join(sec))

pathlib.Path(md_path + ".body").write_text("\n\n".join(sections))
pd.concat({k: v for k, v in summary.items()}).to_csv(f"{out_dir}/headline_summary.csv")
print("done")
