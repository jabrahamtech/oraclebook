"""Idea P2 (PROTOCOL_V6.md): do the ETH / SOL / XRP 5-minute Up/Down markets lag the BTC market
of the same window? Signal at second t: BTC's Up mid moved by >= k over [t - L, t] while the
alt's moved less than k/4 the same way. Buy the alt in BTC's direction at the ask of the first
alt quote known after t (within 3 s, spread <= 5c), taker with the 0.07 fee, hold to resolution;
first signal per alt market. Also: does BTC's recent logit move add out-of-sample information
to the alt's own price (fit on the first half of exploration, log loss on the second)?

Usage: python -I pm_btc_lead.py explore
       python -I pm_btc_lead.py holdout --lookback L --move K --alpha 0.01   (frozen rule, once)
"""

import argparse
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common as C

TD = pd.Timedelta
ALTS = ("eth", "sol", "xrp")
LOOKBACKS = (5, 10)
MOVES = (0.05, 0.10)
INFO_H = (120, 60)
MAX_SPREAD = 0.05
EDGE_GAP = TD("30s")
FILL_WAIT = TD("3s")


def book(coin, mk):
    """Tight two-sided quotes of both tokens for a coin's markets, with window times."""
    q = pd.concat(C.quotes("kachoio", parts=[coin], columns=["mkey", "token", "ts", "bid", "ask"]))
    q = q[(q.bid > 0) & (q.ask < 1) & (q.bid <= q.ask) & (q.ask - q.bid <= MAX_SPREAD)]
    q = q.merge(mk[["mkey", "open_ts", "close_ts"]], on="mkey")
    return q.assign(mid=(q.bid + q.ask) / 2).sort_values("ts").reset_index(drop=True)


def with_change(up, lb):
    """Add the Up mid lb seconds earlier (latest quote at or before, at most 2 s older)."""
    past = up[["mkey", "ts", "mid"]].rename(columns={"ts": "ts_past", "mid": "mid_past"})
    x = up.assign(t_back=up.ts - TD(seconds=lb)).sort_values("t_back")
    x = pd.merge_asof(
        x, past, left_on="t_back", right_on="ts_past", by="mkey", direction="backward",
        tolerance=TD("2s"),
    )
    return x.assign(d=x.mid - x.mid_past, dl=C.logit(x.mid) - C.logit(x.mid_past))


def panel(lb):
    """One row per (alt market, second): the alt's Up mid and change, BTC's change, outcome."""
    import crypto_favourites as CF

    mk, _snap, cut = CF.load()
    mk = mk.assign(y=mk.y.fillna(mk.y_spot))
    btc = with_change(book("btc", mk[mk.coin == "btc"]).query("token == 0"), lb)
    btc = btc[["close_ts", "ts", "d", "dl"]].dropna().rename(
        columns={"ts": "ts_btc", "d": "d_btc", "dl": "dl_btc"}
    )
    out, books = [], {}
    for coin in ALTS:
        m = mk[mk.coin == coin]
        if m.empty:
            continue
        b = book(coin, m)
        books[coin] = b
        alt = with_change(b[b.token == 0], lb).dropna(subset=["d"]).sort_values("ts")
        x = pd.merge_asof(
            alt, btc.sort_values("ts_btc"), left_on="ts", right_on="ts_btc", by="close_ts",
            direction="backward", tolerance=TD("1s"),
        ).dropna(subset=["d_btc"])
        out.append(x.merge(m[["mkey", "split", "y"]], on="mkey").assign(coin=coin))
    d = pd.concat(out, ignore_index=True)
    d = d[d.y.notna()]
    return d.assign(cluster=d.close_ts.dt.strftime("%Y-%m-%d")), books, cut


def trades(d, books, move):
    inside = (d.ts >= d.open_ts + EDGE_GAP) & (d.ts <= d.close_ts - EDGE_GAP)
    up = d.d_btc > 0
    lag = np.where(up, d.d, -d.d) < move / 4
    sig = d[inside & (d.d_btc.abs() >= move) & lag].sort_values("ts").drop_duplicates("mkey")
    sig = sig.assign(token=np.where(sig.d_btc > 0, 0, 1).astype("int8"))
    fills = []
    for coin, g in sig.groupby("coin"):
        q = books[coin][["mkey", "token", "ts", "ask"]].rename(columns={"ts": "ts_fill"})
        fills.append(
            pd.merge_asof(
                g.sort_values("ts"), q.sort_values("ts_fill"), left_on="ts", right_on="ts_fill",
                by=["mkey", "token"], direction="forward", tolerance=FILL_WAIT,
                allow_exact_matches=False, suffixes=("_signal", ""),
            ).dropna(subset=["ask"])
        )
    if not fills:
        return pd.DataFrame()
    t = pd.concat(fills, ignore_index=True)
    t = t.assign(price=t.ask, y=np.where(t.token == 0, t.y, 1 - t.y))
    f = C.poly_fee(t.price)
    return t.assign(ret=(t.y - t.price - f) / (t.price + f))


def info(d):
    """Out-of-sample log-loss gain from adding BTC's logit move to the alt's logit(mid)."""
    e = d[d.split == "explore"]
    half = e.close_ts.quantile(0.5)
    rows = []
    for h in INFO_H:
        g = e[e.ts == e.close_ts - TD(seconds=h)]
        a, b = g[g.close_ts < half], g[g.close_ts >= half]
        if len(a) < 300 or len(b) < 300:
            continue
        b0 = C.logreg(C.logit(a.mid)[:, None], a.y)
        b1 = C.logreg(np.column_stack([C.logit(a.mid), a.dl_btc]), a.y)
        p0 = C.predict(b0, C.logit(b.mid)[:, None])
        p1 = C.predict(b1, np.column_stack([C.logit(b.mid), b.dl_btc]))
        rows.append({"h_s": h, "n": len(g), "btc_coef": b1[2],
                     "gain": C.logloss(p0, b.y) - C.logloss(p1, b.y)})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["explore", "holdout"])
    ap.add_argument("--lookback", type=int, choices=LOOKBACKS)
    ap.add_argument("--move", type=float)
    ap.add_argument("--alpha", type=float, default=0.05)
    a = ap.parse_args()
    if a.mode == "holdout":
        d, books, cut = panel(a.lookback)
        t = trades(d[d.split == "holdout"], books, a.move)
        print(
            f"HOLDOUT L={a.lookback}s move>={a.move} alpha={a.alpha}: "
            f"{C.fmt(C.summarise(t, alpha=a.alpha))}"
        )
        return
    rows, gains = [], []
    for lb in LOOKBACKS:
        d, books, cut = panel(lb)
        e = d[d.split == "explore"]
        for move in MOVES:
            rows.append({"lookback_s": lb, "move": move, **C.summarise(trades(e, books, move))})
        gains.append(info(d).assign(lookback_s=lb))
    print(f"kachoio alts vs BTC, split at close {cut}")
    print("does BTC's recent move add information to the alt's price? (gain > 0 = yes)")
    print(pd.concat(gains).to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    print("\nrule grid (exploration, buy the alt in BTC's direction, 0.07 fee, held)")
    print(pd.DataFrame(rows).to_string(index=False, float_format=lambda v: f"{v:.4f}"))


if __name__ == "__main__":
    main()
