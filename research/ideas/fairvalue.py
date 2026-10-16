"""Idea 1: fair value from the underlying. For "will the coin be up over this window" markets,
a driftless random walk gives P(up) = Phi(ln(S_t / K) / (sigma * sqrt(tau))), with S_t the spot
price known at the sample time, K the window's start price (Kalshi: the published strike), sigma
the trailing 60-minute realised volatility and tau the time left. Does the market price already
contain this, and does buying whichever side the model favours by more than a threshold pay
after costs?

Usage: python -I fairvalue.py <kalshi|kachoio|polyorderbooks> explore
       python -I fairvalue.py <name> holdout --horizon H --theta X   (frozen rule, run once)
"""

import argparse
import math
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common as C

TD = pd.Timedelta
SQRT2 = math.sqrt(2)


def phi(x):
    return 0.5 * (1 + np.vectorize(math.erf)(x / SQRT2))


def model_p(spot, strike, sigma, tau_min):
    # Settlement averages the last minute (Kalshi) or takes a point price (Polymarket); either way
    # a floor of 0.5 minute keeps the variance positive at the last sample.
    s = sigma * np.sqrt(np.maximum(tau_min, 0.5))
    return phi(np.log(spot / strike) / s)


def sample_quotes(m, q, offsets, stale, tokens):
    return C.P.sample(m, q, "close_ts", offsets, stale, tokens=tokens)


def prep_kalshi():
    m, cut = C.markets("kalshi")
    m = m[m.strike.notna()]
    offsets = {f"{k}m": -TD(minutes=k) for k in range(1, 15)}
    s = sample_quotes(m, C.quotes("kalshi"), offsets, TD("59s"), (0,))
    spot = C.with_vol(C.coinbase_btc())
    sp = C.spot_at(spot, s.t)
    s = s.assign(
        S=sp.close.to_numpy(),
        sigma=sp.sigma.to_numpy(),
        age=sp.age.to_numpy(),
        K=s.strike,
        cluster=s.close_ts.dt.strftime("%Y-%m-%d"),
    )
    s["tau"] = (s.close_ts - s.t).dt.total_seconds() / 60
    # Book: YES bid/ask; buying NO costs 1 - YES bid.
    s = s.assign(bid_up=s.bid, ask_up=s.ask, bid_dn=1 - s.ask, ask_dn=1 - s.bid, fee="kx")
    return s, cut


def prep_updown(name):
    m, cut = C.markets(name)
    if name == "kachoio":
        offsets = {f"{k}s": -TD(seconds=k) for k in range(30, 271, 30)}
        stale, parts = TD("5s"), None
    else:
        offsets = None
        stale, parts = TD("15s"), None
    out = []
    groups = m.groupby("coin") if name == "kachoio" else m.groupby(["coin", "length"])
    for key, mg in groups:
        coin = key if isinstance(key, str) else key[0]
        try:
            k = C.with_vol(C.klines(coin))
        except FileNotFoundError:
            continue
        if name == "polyorderbooks":
            length = key[1]
            secs = {
                "5m": range(30, 271, 30),
                "15m": range(60, 841, 60),
                "4h": range(300, 14101, 900),
            }[length]
            offsets = {f"{x}s": -TD(seconds=x) for x in secs}
            parts = [length]
            q = (
                x[x.bid.notna() & x.ask.notna() & (x.bid < x.ask)]
                for x in C.quotes(name, parts=parts)
            )
        else:
            q = C.quotes(name, parts=[coin])
        s = sample_quotes(mg, q, offsets, stale, (0, 1))
        if s.empty:
            continue
        w = s.pivot_table(index=["mkey", "horizon", "t"], columns="token", values=["bid", "ask"])
        w.columns = [f"{a}_{'up' if b == 0 else 'dn'}" for a, b in w.columns]
        w = w.reset_index().merge(mg, on="mkey")
        open_ts = w.open_ts if w.open_ts.notna().all() else w.close_ts - TD(length_minutes(w))
        ks = C.spot_at(k, open_ts)
        sp = C.spot_at(k, w.t)
        end = C.spot_at(k, w.close_ts)
        w = w.assign(
            K=ks.close.to_numpy(),
            S=sp.close.to_numpy(),
            sigma=sp.sigma.to_numpy(),
            age=sp.age.to_numpy(),
            y_spot=(end.close.to_numpy() >= ks.close.to_numpy()),
        )
        out.append(w)
    s = pd.concat(out, ignore_index=True)
    s["tau"] = (s.close_ts - s.t).dt.total_seconds() / 60
    s["cluster"] = s.close_ts.dt.strftime("%Y-%m-%d")
    s["fee"] = "pm"
    return s, cut


def length_minutes(w):
    return {"5m": 5, "15m": 15, "4h": 240}[w.length.iloc[0]] * 60_000_000_000


def add_model(s):
    s = s[s.S.notna() & s.K.notna() & s.sigma.notna() & (s.sigma > 0)].copy()
    s["p"] = model_p(s.S, s.K, s.sigma, s.tau)
    s["mid"] = (s.bid_up + s.ask_up) / 2
    return s


def trades(s, theta):
    """Buy whichever side the model favours by more than theta after fee, at the ask; one row
    per (market, horizon)."""
    fee = C.kalshi_fee if s.fee.iloc[0] == "kx" else C.poly_fee
    rows = []
    for side, ask, pw, yv in (("up", s.ask_up, s.p, s.y), ("dn", s.ask_dn, 1 - s.p, 1 - s.y)):
        ok = ask.between(0, 1, inclusive="neither")
        a = ask.where(ok)
        f = fee(a)
        edge = pw - a - f
        t = s[ok & (edge > theta)].assign(side=side, price=a, fee_paid=f, edge=edge, y=yv)
        rows.append(t)
    t = pd.concat(rows)
    # If both sides somehow qualify, keep the larger edge.
    t = t.sort_values("edge").groupby(["mkey", "horizon"]).tail(1)
    t["ret"] = (t.y - t.price - t.fee_paid) / (t.price + t.fee_paid)
    return t


def explore(s):
    e = s[(s.split == "explore") & s.y.notna()]
    print(f"exploration: {e.mkey.nunique():,} markets, {len(e):,} samples")
    if "y_spot" in e:
        dec = e.drop_duplicates("mkey")
        print(
            f"spot-implied outcome agrees with dataset outcome: "
            f"{(dec.y_spot == (dec.y == 1)).mean():.3f}"
        )
    # Does the model add information beyond the market mid? Realised win rate minus mid, by gap.
    g = e[e.mid.between(0.02, 0.98)].assign(gap=lambda d: d.p - d.mid)
    g["gap_bin"] = pd.cut(g.gap, [-1, -0.2, -0.1, -0.05, -0.02, 0.02, 0.05, 0.1, 0.2, 1])
    t = g.groupby("gap_bin", observed=True).agg(
        n=("y", "size"), mid=("mid", "mean"), p=("p", "mean"), y=("y", "mean")
    )
    print("\nrealised up-rate vs market mid, by model-minus-mid gap (all horizons):")
    print(t.round(3).to_string())
    print("\nrule grid (exploration): buy the model's side at the ask when edge after fee > theta")
    res = []
    for h in sorted(e.horizon.unique(), key=lambda x: int(x[:-1]) * (60 if x.endswith("m") else 1)):
        for theta in (0.0, 0.02, 0.05, 0.10):
            tr = trades(e[e.horizon == h], theta)
            sm = C.summarise(tr)
            res.append({"horizon": h, "theta": theta, **sm})
    r = pd.DataFrame(res)
    print(r.to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    return r


def info(s):
    """Does spot add information on top of the market mid? Fit y ~ logit(mid) [+ z] on the first
    half of exploration (by close), score log loss on the second half."""
    e = s[(s.split == "explore") & s.y.notna() & s.mid.between(0.01, 0.99) & (s.age <= TD("5s"))]
    e = e.assign(z=np.log(e.S / e.K) / (e.sigma * np.sqrt(np.maximum(e.tau, 0.5))))
    half = e.close_ts.quantile(0.5)
    rows = []
    for h, g in e.groupby("horizon"):
        a, b = g[g.close_ts < half], g[g.close_ts >= half]
        if len(a) < 300 or len(b) < 300:
            continue
        b0 = C.logreg(C.logit(a.mid)[:, None], a.y)
        b1 = C.logreg(np.column_stack([C.logit(a.mid), a.z]), a.y)
        p0 = C.predict(b0, C.logit(b.mid)[:, None])
        p1 = C.predict(b1, np.column_stack([C.logit(b.mid), b.z]))
        rows.append(
            {
                "horizon": h,
                "n_fit": len(a),
                "n_test": len(b),
                "mkt_slope": b0[1],
                "z_coef": b1[2],
                "logloss_mid": C.logloss(b.mid, b.y),
                "logloss_m0": C.logloss(p0, b.y),
                "logloss_m1": C.logloss(p1, b.y),
            }
        )
    r = pd.DataFrame(rows)
    r["gain_m1_vs_mid"] = r.logloss_mid - r.logloss_m1
    print("out-of-sample (second half of exploration) log loss; gain > 0 means spot adds info")
    print(r.to_string(index=False, float_format=lambda v: f"{v:.4f}"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("mode", choices=["explore", "info", "holdout"])
    ap.add_argument("--horizon")
    ap.add_argument("--theta", type=float)
    ap.add_argument("--alpha", type=float, default=0.05)
    a = ap.parse_args()
    s, cut = prep_kalshi() if a.name == "kalshi" else prep_updown(a.name)
    s = add_model(s)
    print(f"{a.name}: split at close {cut}; median spot age {s.age.median()}")
    if a.mode == "explore":
        explore(s)
        return
    if a.mode == "info":
        info(s)
        return
    h = s[(s.split == "holdout") & s.y.notna() & (s.horizon == a.horizon)]
    tr = trades(h, a.theta)
    print(
        f"HOLDOUT {a.name} horizon={a.horizon} theta={a.theta} alpha={a.alpha}: "
        f"{C.fmt(C.summarise(tr, alpha=a.alpha))}"
    )


if __name__ == "__main__":
    main()
