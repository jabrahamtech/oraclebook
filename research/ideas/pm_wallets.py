"""Idea P3 (PROTOCOL_V6.md): does trading skill persist on Polymarket's 5-minute crypto Up/Down
markets? Polymarket's public tape names the wallet behind every taker trade (Kalshi's does not).

Wallet edge: per trade direction × (y_Up − p_Up), averaged per market, then over markets; a
wallet is eligible with >= 20 markets in the window scored; its t is across markets.
Persistence: Spearman rank correlation of edge between the two halves of exploration days, CI
from resampling wallets. Rule P3(t*): copy the first trade per market (>= 10 s before the close)
of every wallet with t >= t* in the scoring window, filled at the next taker BUY of the same side
by another wallet within 5 s (a strictly later second), 0.07 fee, held to resolution.

Data: research/data/polymarket_api.py <raw> --assets BTC,ETH --minutes 5 --start 2026-09-24
      --end 2026-10-15

Usage: python -I pm_wallets.py explore
       python -I pm_wallets.py holdout --t-star T --alpha 0.01   (frozen rule, run once)
"""

import argparse
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common as C

TD = pd.Timedelta
SERIES = ("btc-5m", "eth-5m")
MIN_MARKETS = 20
T_STARS = (2.0, 3.0)
FILL_WAIT = TD("5s")
LAST_ENTRY = TD("10s")


def load():
    ms, ts = [], []
    for s in SERIES:
        folder = C.RAW / "polymarket_api" / s
        if not (folder / "trades.parquet").exists():
            continue
        ms.append(pd.read_parquet(folder / "markets.parquet"))
        ts.append(pd.read_parquet(folder / "trades.parquet"))
    if not ms:
        raise FileNotFoundError("no polymarket_api series under research/data/raw")
    m = pd.concat(ms, ignore_index=True)
    t = pd.concat(ts, ignore_index=True).merge(
        m[["condition_id", "up_token", "close_ts", "y"]], on="condition_id"
    )
    t["ts"] = pd.to_datetime(t.ts, unit="s", utc=True)
    t["is_up"] = t.token == t.up_token
    t["p_up"] = np.where(t.is_up, t.price, 1 - t.price)
    t["dir"] = np.where(t.is_up == (t.side == "BUY"), 1, -1)
    t["edge"] = t.dir * (t.y - t.p_up)
    days = np.sort(m.close_ts.dt.floor("D").unique())
    cut = days[int(len(days) * C.EXPLORE_SHARE)]
    mid = days[int(len(days) * C.EXPLORE_SHARE / 2)]
    t["day"] = t.close_ts.dt.floor("D")
    t["window"] = np.where(t.day >= cut, "holdout", np.where(t.day >= mid, "explore_b", "explore_a"))
    return t.sort_values("ts").reset_index(drop=True), cut


def scores(t):
    per = t.groupby(["wallet", "condition_id"]).edge.mean().groupby("wallet")
    s = pd.DataFrame({"markets": per.size(), "mean": per.mean(), "sd": per.std(ddof=1)})
    s["t"] = s["mean"] / (s.sd / np.sqrt(s.markets))
    return s[s.markets >= MIN_MARKETS]


def persistence(a, b, reps=2000):
    both = scores(a).join(scores(b), lsuffix="_a", rsuffix="_b", how="inner")
    if len(both) < 10:
        return {"wallets": len(both)}
    ra, rb = both["mean_a"].rank().to_numpy(), both["mean_b"].rank().to_numpy()
    rho = np.corrcoef(ra, rb)[0, 1]
    rng = np.random.default_rng(C.SEED)
    idx = rng.integers(0, len(both), size=(reps, len(both)))
    boot = [
        np.corrcoef(both["mean_a"].to_numpy()[i].argsort().argsort(),
                    both["mean_b"].to_numpy()[i].argsort().argsort())[0, 1]
        for i in idx
    ]
    lo, hi = np.percentile(boot, [2.5, 97.5])
    top = both[both.t_a >= 2]
    return {"wallets": len(both), "spearman": rho, "lo": lo, "hi": hi,
            "t>=2_wallets": len(top), "t>=2_mean_edge_next": top.mean_b.mean()}


def copies(score_window, eval_window, t_star):
    s = scores(score_window)
    chosen = s.index[s.t >= t_star]
    e = eval_window[eval_window.wallet.isin(chosen) & (eval_window.close_ts - eval_window.ts >= LAST_ENTRY)]
    sig = e.drop_duplicates(["wallet", "condition_id"])[["wallet", "condition_id", "ts", "dir", "y",
                                                           "close_ts"]]
    sig = sig.assign(want_up=sig.dir > 0)
    buys = eval_window[eval_window.side == "BUY"][["condition_id", "is_up", "ts", "price", "wallet"]]
    fills = []
    for w, g in sig.groupby("wallet"):
        b = buys[buys.wallet != w].rename(columns={"ts": "ts_fill", "wallet": "filler",
                                                   "is_up": "want_up"})
        fills.append(
            pd.merge_asof(
                g.sort_values("ts"), b.sort_values("ts_fill"), left_on="ts", right_on="ts_fill",
                by=["condition_id", "want_up"], direction="forward", tolerance=FILL_WAIT,
                allow_exact_matches=False,
            ).dropna(subset=["price"])
        )
    if not fills:
        return pd.DataFrame()
    x = pd.concat(fills, ignore_index=True)
    x = x.assign(y=np.where(x.want_up, x.y, 1 - x.y))
    f = C.poly_fee(x.price)
    return x.assign(
        ret=(x.y - x.price - f) / (x.price + f),
        cluster=x.close_ts.dt.strftime("%Y-%m-%d %H"),
        day=x.close_ts.dt.strftime("%Y-%m-%d"),
    )


def report(x, alpha=0.05):
    hour = C.summarise(x, alpha=alpha)
    day = C.summarise(x, cluster_col="day", alpha=alpha)
    return {**hour, "day_lo": day.get("lo"), "day_hi": day.get("hi"), "days": day.get("clusters")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["explore", "holdout"])
    ap.add_argument("--t-star", type=float)
    ap.add_argument("--alpha", type=float, default=0.05)
    a = ap.parse_args()
    t, cut = load()
    print(f"{t.condition_id.nunique():,} markets, {len(t):,} taker trades, "
          f"{t.wallet.nunique():,} wallets; holdout from {cut}")
    xa, xb, ho = (t[t.window == w] for w in ("explore_a", "explore_b", "holdout"))
    if a.mode == "explore":
        print("persistence of wallet edge, first vs second half of exploration:")
        print(persistence(xa, xb))
        rows = [{"t_star": ts, **report(copies(xa, xb, ts))} for ts in T_STARS]
        print("\nrule grid (score on exploration half 1, copy on half 2; 0.07 fee, held)")
        print(pd.DataFrame(rows).to_string(index=False, float_format=lambda v: f"{v:.4f}"))
        return
    x = copies(pd.concat([xa, xb]), ho, a.t_star)
    r = report(x, alpha=a.alpha)
    print(f"HOLDOUT t*={a.t_star} alpha={a.alpha}: {C.fmt(r)} "
          f"(day clusters {r.get('days')}: [{(r.get('day_lo') or 0) * 100:+.2f}, "
          f"{(r.get('day_hi') or 0) * 100:+.2f}])")


if __name__ == "__main__":
    main()
