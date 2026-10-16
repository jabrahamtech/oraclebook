"""Shared helpers for the idea tests: panels, the exploration/holdout split, spot prices without
look-ahead, costs and clustered bootstrap. See PROTOCOL.md."""

import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "data"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "flb"))
import flb_lib as L
import panels as P

ROOT = pathlib.Path(__file__).resolve().parents[1]
PANELS = ROOT / "data" / "panels"
RAW = ROOT / "data" / "raw"
UTC = "datetime64[us, UTC]"
SEED = 20261015
EXPLORE_SHARE = 0.6


def markets(name):
    """Panel markets with a `split` column: 'explore' (earliest 60% by close) or 'holdout'."""
    m = P.load_markets(PANELS, name)
    cut = m.close_ts.quantile(EXPLORE_SHARE)
    return m.assign(split=np.where(m.close_ts < cut, "explore", "holdout")), cut


def quotes(name, parts=None, columns=None):
    return P.quote_parts(PANELS, name, parts=parts, columns=columns)


def poly_fee(p):
    return 0.07 * p * (1 - p)


def kalshi_fee(p):
    return L.kalshi_fee(p)


def klines(coin):
    """Binance 1-minute klines for a coin: index = minute start (UTC), columns open/close/volume,
    plus `known` = the time the candle's close became known (start + 1 minute)."""
    files = sorted((RAW / "binance_klines_1m").glob(f"*__{coin.upper()}USDT-1m-*.csv"))
    if not files:
        raise FileNotFoundError(coin)
    frames = []
    for f in files:
        k = pd.read_csv(
            f,
            header=None,
            usecols=[0, 1, 4, 5],
            names=["t", "open", "close", "volume"],
            dtype={"t": "object"},
        )
        k = k[k.t != "open_time"]
        t = k.t.astype("int64")
        unit = "us" if t.iloc[0] > 10**14 else "ms"
        frames.append(
            pd.DataFrame(
                {
                    "start": pd.to_datetime(t, unit=unit, utc=True).astype(UTC),
                    "open": k.open.astype(float),
                    "close": k.close.astype(float),
                    "volume": k.volume.astype(float),
                }
            )
        )
    k = pd.concat(frames).drop_duplicates("start").sort_values("start").reset_index(drop=True)
    return k.assign(known=k.start + pd.Timedelta(minutes=1))


def coinbase_btc():
    c = pd.read_parquet(RAW / "github_kalshi/data/external/btc_usd_1m_coinbase.parquet")
    c = c.rename(columns={"ts": "start"}).sort_values("start").reset_index(drop=True)
    c["start"] = c.start.astype(UTC)
    return c.assign(known=c.start + pd.Timedelta(minutes=1))


def with_vol(k, window=60):
    """Add sigma: std of 1-minute log returns over the trailing `window` closed candles (known at
    `known`), in log-return units per sqrt(minute)."""
    r = np.log(k.close).diff()
    return k.assign(sigma=r.rolling(window, min_periods=window // 2).std())


def spot_at(k, times):
    """Latest candle close known at or before each time (no look-ahead): returns close, sigma and
    the age of that close."""
    t = pd.DataFrame({"t": pd.Series(times).astype(UTC).to_numpy()}).reset_index()
    cols = ["known", "close"] + (["sigma"] if "sigma" in k else [])
    s = pd.merge_asof(
        t.sort_values("t"), k[cols], left_on="t", right_on="known", direction="backward"
    )
    s = s.sort_values("index")
    return s.assign(age=s.t - s.known).set_index("index")


def boot_ci(values, clusters, b=4000, seed=SEED, alpha=0.05):
    """Mean of per-trade values with a percentile CI resampling clusters."""
    rng = np.random.default_rng(seed)
    ew, (lo, hi), _cl, _ci, k = L.cluster_boot(
        np.asarray(values, float), np.asarray(clusters), rng, b=b
    )
    if alpha != 0.05 and k >= 2:
        df = pd.DataFrame({"v": values, "c": clusters}).groupby("c").v.agg(["sum", "count"])
        idx = rng.integers(0, k, size=(b, k))
        bs = df["sum"].to_numpy()[idx].sum(1) / df["count"].to_numpy()[idx].sum(1)
        lo, hi = np.percentile(bs, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return ew, lo, hi, k


def summarise(trades, ret_col="ret", cluster_col="cluster", alpha=0.05):
    """trades: one row per trade with a return per $1 and a cluster id."""
    if trades.empty:
        return {"n": 0}
    ew, lo, hi, k = boot_ci(trades[ret_col], trades[cluster_col].astype(str), alpha=alpha)
    return {
        "n": len(trades),
        "clusters": k,
        "mean_price": trades.get("price", pd.Series()).mean(),
        "win_rate": trades.get("y", pd.Series()).mean(),
        "ret": ew,
        "lo": lo,
        "hi": hi,
    }


def fmt(s):
    if not s.get("n"):
        return "n=0"
    return (
        f"n={s['n']:,} clusters={s['clusters']} ret={s['ret'] * 100:+.2f}% "
        f"[{s['lo'] * 100:+.2f}, {s['hi'] * 100:+.2f}]"
    )


def logit(p, eps=1e-4):
    p = np.clip(np.asarray(p, float), eps, 1 - eps)
    return np.log(p / (1 - p))


def logreg(X, y, iters=50, ridge=1e-6):
    """Plain logistic regression by Newton; X without intercept. Returns coefficients
    [intercept, *slopes]."""
    X = np.column_stack([np.ones(len(X)), np.asarray(X, float)])
    y = np.asarray(y, float)
    b = np.zeros(X.shape[1])
    for _ in range(iters):
        mu = 1 / (1 + np.exp(-(X @ b)))
        g = X.T @ (y - mu) - ridge * b
        h = (X * (mu * (1 - mu))[:, None]).T @ X + ridge * np.eye(len(b))
        step = np.linalg.solve(h, g)
        b += step
        if np.abs(step).max() < 1e-10:
            break
    return b


def predict(b, X):
    X = np.column_stack([np.ones(len(X)), np.asarray(X, float)])
    return 1 / (1 + np.exp(-(X @ b)))


def logloss(p, y):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    y = np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
