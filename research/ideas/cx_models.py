"""Round 4 tests on the cx.py dataset (PROTOCOL_V4.md): G1 gradient boosting, F1 Chronos-Bolt
zero-shot, B1 bull regimes.

Usage: python -I cx_models.py chronos --model DIR      # adds the f1 column (cached)
       python -I cx_models.py explore
       python -I cx_models.py holdout --rule G1|F1|B1 --edge E [--asset BTC] [--max-age S]
                                       [--alpha A]
"""

import argparse
import math
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import cx
import kx

SPLIT = pd.Timestamp("2026-01-01", tz="UTC")
FEATURES = [
    "z",
    "left",
    "ret_5m",
    "ret_15m",
    "ret_60m",
    "ret_1d",
    "ret_7d",
    "ret_30d",
    "vol_60m",
    "vol_1d",
    "flow_imb",
    "flow_n",
    "last",
    "is_btc",
]
PHI = np.vectorize(lambda z: 0.5 * (1 + math.erf(z / math.sqrt(2))))
CHRONOS = cx.OUT / "chronos_f1.parquet"
BULL = 0.10


def data():
    d = pd.read_parquet(cx.OUT / "dataset.parquet")
    d = d.assign(is_btc=(d.asset == "BTC").astype(float), month=d.close.dt.strftime("%Y-%m"))
    if CHRONOS.exists():
        f = pd.read_parquet(CHRONOS)
        d = d.merge(f, on=["event", "left", "strike"], how="left")
    return d


def gbm():
    from sklearn.ensemble import HistGradientBoostingClassifier

    return HistGradientBoostingClassifier(
        max_iter=300,
        learning_rate=0.05,
        max_leaf_nodes=15,
        min_samples_leaf=50,
        l2_regularization=1.0,
        random_state=kx.SEED,
    )


def walk_forward(d):
    """Each exploration month predicted by a model fitted on all earlier months (>= 3)."""
    out = pd.Series(np.nan, index=d.index)
    months = sorted(d.month.unique())
    for i, mo in enumerate(months):
        if i < 3:
            continue
        tr, te = d[d.month < mo], d[d.month == mo]
        clf = gbm().fit(tr[FEATURES], tr.y)
        out[te.index] = clf.predict_proba(te[FEATURES])[:, 1]
    return out


def chronos(model_dir):
    """F1: Chronos-Bolt quantiles of the price at the close from the last 512 minutes before t;
    P(above K) from a normal fitted to the median and the 10–90% range of the forecast."""
    import torch
    from chronos import BaseChronosPipeline

    pipe = BaseChronosPipeline.from_pretrained(model_dir, device_map="cpu", dtype=torch.float32)
    d = pd.read_parquet(cx.OUT / "dataset.parquet")
    keys = d[["asset", "event", "close", "left"]].drop_duplicates()
    rows = []
    for asset, g in keys.groupby("asset"):
        px = cx.spot(asset)
        for left, gg in g.groupby("left"):
            ctx, idx = [], []
            for _, r in gg.iterrows():
                t = (r.close - pd.Timedelta(minutes=int(left))).floor("min")
                s = px.loc[:t].iloc[-513:-1]
                if len(s) == 512:
                    ctx.append(torch.tensor(s.to_numpy(), dtype=torch.float32))
                    idx.append(r.event)
            for i in range(0, len(ctx), 256):
                q, _ = pipe.predict_quantiles(
                    torch.stack(ctx[i : i + 256]),
                    prediction_length=int(left),
                    quantile_levels=[0.1, 0.5, 0.9],
                )
                q = q[:, -1, :].numpy()
                for ev, (q10, q50, q90) in zip(idx[i : i + 256], q, strict=True):
                    rows.append((ev, left, q10, q50, q90))
        print(f"{asset}: forecasts done", flush=True)
    f = pd.DataFrame(rows, columns=["event", "left", "q10", "q50", "q90"])
    m = d[["event", "left", "strike"]].merge(f, on=["event", "left"])
    sd = (np.log(m.q90) - np.log(m.q10)) / 2.5631
    m["f1"] = 1 - PHI((np.log(m.strike) - np.log(m.q50)) / sd)
    m[["event", "left", "strike", "f1"]].to_parquet(CHRONOS, index=False)
    print(f"saved {len(m):,} Chronos probabilities", flush=True)


def takers(d, prob, edge, max_age=None):
    """Buy the side whose probability beats the ask + fee by `edge`. With max_age (seconds) only
    asks printed that recently count, so a model that sees the latest spot cannot pick up quotes
    that had already moved away."""
    out = []
    for side in ("yes", "no"):
        p = d[f"ask_{side}"]
        if max_age is not None:
            p = p.where(d[f"age_{side}"] <= max_age)
        q = prob if side == "yes" else 1 - prob
        fee = 0.07 * p * (1 - p)
        sel = (q - p - fee > edge) & p.notna() & q.notna()
        x = d[sel]
        y = x.y if side == "yes" else 1 - x.y
        out.append(
            x.assign(side=side, count=1.0, cost=p[sel] + fee[sel], pnl=y - p[sel] - fee[sel])
        )
    return pd.concat(out)


def bull(d):
    return d.ret_30d > np.log1p(BULL)


def b1(d):
    """B1: in bull regimes, buy YES at the ask on near-the-money strikes (|z| < 1)."""
    x = d[bull(d) & (d.z.abs() < 1) & d.ask_yes.notna()]
    fee = 0.07 * x.ask_yes * (1 - x.ask_yes)
    return x.assign(count=1.0, cost=x.ask_yes + fee, pnl=x.y - x.ask_yes - fee)


def brier(x, col):
    ok = x[col].notna() & x["last"].notna()
    return (
        ((x.loc[ok, col] - x.loc[ok, "y"]) ** 2).mean(),
        ((x.loc[ok, "last"] - x.loc[ok, "y"]) ** 2).mean(),
        ok.sum(),
    )


def explore():
    d = data()
    e = d[d.close < SPLIT].copy()
    print(
        f"exploration: {len(e):,} rows, {e.event.nunique()} events, "
        f"{e.month.min()} → {e.month.max()}"
    )
    print(
        f"bull-regime share (30-day return > {BULL:.0%}): {bull(e).mean():.1%} of rows, "
        f"{e[bull(e)].event.nunique()} events"
    )
    e["g1"] = walk_forward(e)
    for col in ("g1", "f1"):
        if col not in e or e[col].isna().all():
            continue
        m_, k_, n_ = brier(e, col)
        print(f"{col}: Brier model {m_:.4f} vs market {k_:.4f} on {n_:,} rows")
        for regime, sub in (("all", e), ("bull", e[bull(e)]), ("not bull", e[~bull(e)])):
            for edge in (0.0, 0.03, 0.05, 0.10):
                f = takers(sub, sub[col], edge)
                print(f"  {regime:8s} edge {edge:.2f}: {kx.fmt(kx.ratio_boot(f, 'event'))}")
    print("market calibration on near-the-money strikes (|z| < 1), by regime:")
    near = e[e.z.abs() < 1].dropna(subset=["last"])
    for regime, sub in (("bull", near[bull(near)]), ("not bull", near[~bull(near)])):
        print(
            f"  {regime:8s}: mean price {sub['last'].mean():.3f}, "
            f"YES rate {sub.y.mean():.3f}, n {len(sub):,}"
        )
    print(f"B1 (bull, buy YES near the money): {kx.fmt(kx.ratio_boot(b1(e), 'event'))}")


def holdout(rule, edge, alpha, asset, max_age):
    d = data()
    if asset:
        d = d[d.asset == asset]
    e, h = d[d.close < SPLIT], d[d.close >= SPLIT].copy()
    if rule == "G1":
        h["g1"] = gbm().fit(e[FEATURES], e.y).predict_proba(h[FEATURES])[:, 1]
        f = takers(h, h.g1, edge, max_age)
    elif rule == "F1":
        f = takers(h, h.f1, edge, max_age)
    else:
        f = b1(h)
    print(
        f"HOLDOUT {rule} {asset or 'all'} edge {edge} max_age {max_age}: "
        f"{h.event.nunique()} events {h.close.min():%Y-%m-%d} → {h.close.max():%Y-%m-%d}: "
        f"{kx.fmt(kx.ratio_boot(f, 'event', alpha=alpha))}"
    )


def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("mode", choices=["chronos", "explore", "holdout"])
    ap.add_argument("--model", help="local Chronos-Bolt directory (config.json, model.safetensors)")
    ap.add_argument("--rule", choices=["G1", "F1", "B1"])
    ap.add_argument("--edge", type=float, default=0.0)
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--asset", choices=["BTC", "ETH"])
    ap.add_argument("--max-age", type=float, help="only asks printed this many seconds ago or less")
    a = ap.parse_args()
    if a.mode == "chronos":
        chronos(a.model)
    elif a.mode == "explore":
        explore()
    else:
        holdout(a.rule, a.edge, a.alpha, a.asset, a.max_age)


if __name__ == "__main__":
    main()
