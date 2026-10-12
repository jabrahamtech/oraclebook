"""Shared FLB / calibration machinery.

Standard observation table columns:
  dataset, horizon, market_id, cluster_id, side, p_cal, p_buy, y
p_cal  = price used for calibration (reported/last or quote midpoint)
p_buy  = price paid to buy the contract (reported price or best ask)
y      = 1 if the contract paid $1, else 0
"""
import math

import numpy as np
import pandas as pd

SEED = 20261012
B = 5000

DECILES = [(i / 10, (i + 1) / 10) for i in range(10)]
FINE = [(0.0, 0.01), (0.01, 0.02), (0.02, 0.05), (0.05, 0.10),
        (0.90, 0.95), (0.95, 0.98), (0.98, 0.99), (0.99, 1.0)]
HEADLINE = [(0.0, 0.10, True), (0.90, 0.9999999, True)]


def poly_fee(p, rate):
    return rate * p * (1 - p)


def kalshi_fee(p):
    # ceil-to-cent of 0.07 * p * (1-p) for a 1-contract order; small epsilon guards float noise
    return np.ceil(np.round(0.07 * p * (1 - p) * 100, 9)) / 100


def ret_per_dollar(y, p, fee=0.0):
    cost = p + fee
    return (y - cost) / cost


def in_bin(p, lo, hi, incl_hi=False):
    if incl_hi:
        return (p > max(lo, 0.0) if lo == 0 else p >= lo) & (p <= hi)
    if hi >= 1.0:
        return (p >= lo) & (p < 1.0)
    return (p >= lo) & (p < hi) if lo > 0 else (p > 0) & (p < hi)


def cluster_boot(values, clusters, rng, b=B):
    """Return point estimates and percentile CIs for (equal-weight mean, cluster-mean-of-means),
    resampling clusters with replacement."""
    df = pd.DataFrame({"v": values, "c": clusters})
    g = df.groupby("c")["v"].agg(["sum", "count"])
    sums, cnts = g["sum"].to_numpy(), g["count"].to_numpy()
    means = sums / cnts
    k = len(g)
    ew = sums.sum() / cnts.sum()
    cl = means.mean()
    if k < 2:
        return ew, (np.nan, np.nan), cl, (np.nan, np.nan), k
    ew_b, cl_b = [], []
    for start in range(0, b, 500):
        idx = rng.integers(0, k, size=(min(500, b - start), k))
        ew_b.append(sums[idx].sum(1) / cnts[idx].sum(1))
        cl_b.append(means[idx].mean(1))
    ew_b, cl_b = np.concatenate(ew_b), np.concatenate(cl_b)
    q = [2.5, 97.5]
    return ew, tuple(np.percentile(ew_b, q)), cl, tuple(np.percentile(cl_b, q)), k


def _logit(p):
    p = np.clip(p, 1e-3, 1 - 1e-3)
    return np.log(p / (1 - p))


def logistic_fit(x, y, w=None, iters=30):
    """Weighted 2-parameter logistic regression y ~ a + b x via Newton. w may be (R, N)."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    if w is None:
        w = np.ones((1, len(x)))
    w = np.atleast_2d(w).astype(float)
    r = w.shape[0]
    beta = np.zeros((r, 2))
    beta[:, 1] = 1.0
    for _ in range(iters):
        eta = beta[:, :1] + beta[:, 1:] * x[None, :]
        mu = 1 / (1 + np.exp(-eta))
        s = w * mu * (1 - mu)
        g0 = (w * (y[None, :] - mu)).sum(1)
        g1 = (w * (y[None, :] - mu) * x[None, :]).sum(1)
        h00 = s.sum(1)
        h01 = (s * x[None, :]).sum(1)
        h11 = (s * x[None, :] ** 2).sum(1)
        det = h00 * h11 - h01 ** 2
        det = np.where(np.abs(det) < 1e-12, np.nan, det)
        d0 = (h11 * g0 - h01 * g1) / det
        d1 = (-h01 * g0 + h00 * g1) / det
        beta[:, 0] += d0
        beta[:, 1] += d1
        if np.nanmax(np.abs(np.concatenate([d0, d1]))) < 1e-9:
            break
    return beta


def calib_slope(p, y, clusters, rng, b=B, chunk=250):
    x = _logit(np.asarray(p, float))
    y = np.asarray(y, float)
    codes, uniq = pd.factorize(pd.Series(clusters))
    k = len(uniq)
    a0, b0 = logistic_fit(x, y)[0]
    slopes, ints = [], []
    for start in range(0, b, chunk):
        n = min(chunk, b - start)
        draws = rng.integers(0, k, size=(n, k))
        cw = np.zeros((n, k))
        np.add.at(cw, (np.repeat(np.arange(n), k), draws.ravel()), 1)
        w = cw[:, codes]
        beta = logistic_fit(x, y, w)
        ints.append(beta[:, 0])
        slopes.append(beta[:, 1])
    s = np.concatenate(slopes)
    i = np.concatenate(ints)
    return (b0, tuple(np.nanpercentile(s, [2.5, 97.5])), a0, tuple(np.nanpercentile(i, [2.5, 97.5])))


def brier(p, y):
    p = np.asarray(p, float)
    y = np.asarray(y, float)
    return float(np.mean((p - y) ** 2))


def bin_table(obs, bins, fee_fns, rng, price_col="p_buy", cal_col="p_cal"):
    """obs: one horizon of one dataset. fee_fns: {label: fn(p)->fee per contract}."""
    rows = []
    for b in bins:
        lo, hi = b[0], b[1]
        incl = len(b) > 2 and b[2]
        label = (f"({lo:.2f},{hi:.2f}]" if lo == 0 else f"[{lo:.2f},{hi:.2f}]") if incl else f"[{lo:.2f},{hi:.2f})"
        sel = obs[in_bin(obs[price_col].to_numpy(), lo, hi, incl)]
        if len(sel) == 0:
            rows.append({"bin": label, "n_contracts": 0})
            continue
        p = sel[price_col].to_numpy()
        y = sel["y"].to_numpy()
        r = ret_per_dollar(y, p)
        ew, ew_ci, cl, cl_ci, k = cluster_boot(r, sel["cluster_id"].to_numpy(), rng)
        row = {"bin": label, "n_contracts": len(sel), "n_markets": sel.market_id.nunique(),
                   "n_clusters": k, "mean_price": p.mean(), "mean_cal_price": sel[cal_col].mean(), "win_rate": y.mean(),
                   "ret_ew": ew, "ret_ew_lo": ew_ci[0], "ret_ew_hi": ew_ci[1],
                   "ret_cl": cl, "ret_cl_lo": cl_ci[0], "ret_cl_hi": cl_ci[1]}
        for lab, fn in fee_fns.items():
            rf = ret_per_dollar(y, p, fn(p))
            e2, e2ci, c2, _c2ci, _ = cluster_boot(rf, sel["cluster_id"].to_numpy(), rng)
            row[f"ret_ew_{lab}"] = e2
            row[f"ret_ew_{lab}_lo"] = e2ci[0]
            row[f"ret_ew_{lab}_hi"] = e2ci[1]
            row[f"ret_cl_{lab}"] = c2
        rows.append(row)
    return pd.DataFrame(rows)


def fmt_ci(v, lo, hi, pct=True):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "–"
    if pct:
        return f"{v*100:+.1f}% [{lo*100:+.1f}, {hi*100:+.1f}]"
    return f"{v:.3f} [{lo:.3f}, {hi:.3f}]"
