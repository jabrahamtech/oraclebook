"""Round-2 helpers: Kalshi API markets + trade tapes, the 2026-09-25 split, Kalshi fees and
dollar-weighted returns with cluster bootstrap. See PROTOCOL_V2.md."""

import json
import pathlib

import numpy as np
import pandas as pd

RAW = pathlib.Path(__file__).resolve().parents[1] / "data" / "raw" / "kalshi_api"
SPLIT = pd.Timestamp("2026-09-25", tz="UTC")
SEED = 20261016


def series_meta():
    s = json.loads((RAW / "_series.json").read_text())
    return {x["ticker"]: x for x in s}


def fee_rates(series):
    """(taker rate, maker rate) per contract as rate * p * (1 - p)."""
    x = series_meta().get(series, {})
    mult = float(x.get("fee_multiplier") or 1)
    maker = 0.0175 * mult if x.get("fee_type") == "quadratic_with_maker_fees" else 0.0
    return 0.07 * mult, maker


def available():
    return sorted(p.name for p in RAW.iterdir() if (p / "trades.parquet").exists())


def load(series_list, trades=True, root=RAW):
    ms, ts = [], []
    for s in series_list:
        d = root / s
        if not (d / "markets.parquet").exists():
            continue
        m = pd.read_parquet(d / "markets.parquet")
        m = m[m.result.isin(["yes", "no"])].copy()
        taker, maker = fee_rates(s)
        m = m.assign(series=s, fee_taker=taker, fee_maker=maker)
        ms.append(m)
        if trades and (d / "trades.parquet").exists():
            ts.append(pd.read_parquet(d / "trades.parquet"))
    m = pd.concat(ms, ignore_index=True)
    for c in (
        "open_time",
        "close_time",
        "occurrence_datetime",
        "settlement_ts",
        "expected_expiration_time",
    ):
        if c in m:
            m[c] = pd.to_datetime(m[c], utc=True, format="ISO8601", errors="coerce")
    m["y"] = (m.result == "yes").astype(float)
    m["split"] = np.where(m.close_time < SPLIT, "explore", "holdout")
    if not trades:
        return m, None
    t = pd.concat(ts, ignore_index=True).drop_duplicates("trade_id")
    t["ts"] = pd.to_datetime(t.created_time, utc=True, format="ISO8601")
    t = t.drop(columns=["created_time"]).merge(m, on="ticker", how="inner")
    return m, t


def sides(t):
    """Per trade: the side the taker bought, its price, and the maker's mirror position."""
    yes = t.taker_side == "yes"
    p_t = np.where(yes, t.yes_price, 1 - t.yes_price)
    y_t = np.where(yes, t.y, 1 - t.y)
    return t.assign(p_taker=p_t, y_taker=y_t, p_maker=1 - p_t, y_maker=1 - y_t)


def pnl(t, who):
    """Dollar P&L and cost per trade for `who` in {'taker', 'maker'}, fees included."""
    p, y = t[f"p_{who}"], t[f"y_{who}"]
    fee = t[f"fee_{who}"] * p * (1 - p)
    cost = (p + fee) * t["count"]
    return t.assign(cost=cost, pnl=(y - p - fee) * t["count"])


def ratio_boot(d, cluster, b=4000, seed=SEED, alpha=0.05):
    """Dollar-weighted return sum(pnl)/sum(cost) with a percentile CI resampling clusters."""
    if d.empty:
        return {"n": 0}
    g = d.groupby(cluster)[["pnl", "cost"]].sum()
    k = len(g)
    est = g.pnl.sum() / g.cost.sum()
    if k < 2:
        return {
            "n": len(d),
            "dollars": float(d.cost.sum()),
            "clusters": k,
            "ret": est,
            "lo": np.nan,
            "hi": np.nan,
        }
    rng = np.random.default_rng(seed)
    p, c = g.pnl.to_numpy(), g.cost.to_numpy()
    bs = []
    for start in range(0, b, 500):
        idx = rng.integers(0, k, size=(min(500, b - start), k))
        bs.append(p[idx].sum(1) / c[idx].sum(1))
    lo, hi = np.percentile(np.concatenate(bs), [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return {
        "n": len(d),
        "contracts": float(d["count"].sum()),
        "dollars": float(d.cost.sum()),
        "clusters": k,
        "ret": est,
        "lo": lo,
        "hi": hi,
    }


def fmt(s):
    if not s.get("n"):
        return "n=0"
    return (
        f"trades={s['n']:,} ${s['dollars']:,.0f} clusters={s['clusters']} "
        f"ret={s['ret'] * 100:+.2f}% [{s['lo'] * 100:+.2f}, {s['hi'] * 100:+.2f}]"
    )
