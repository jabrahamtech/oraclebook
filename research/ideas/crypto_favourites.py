"""Idea 5: are favourites in 5-minute crypto Up/Down markets underpriced once the close calls the
publisher left without an outcome are put back? The publisher infers outcomes from the final
tick and leaves ~12% null; dropping them removes exactly the markets where favourites lose.
Here the missing outcomes are rebuilt from Binance (end price >= start price), after measuring
how often Binance agrees with the publisher where the publisher did give one.

Usage: python -I crypto_favourites.py explore
       python -I crypto_favourites.py holdout --horizon H --lo X   (frozen rule, run once)
"""

import argparse
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common as C

TD = pd.Timedelta
HORIZONS = {"240s": 240, "120s": 120, "60s": 60, "30s": 30}


def load():
    m, cut = C.markets("kachoio")
    out = []
    for coin, mg in m.groupby("coin"):
        k = C.klines(coin)
        start = C.spot_at(k, mg.open_ts)
        end = C.spot_at(k, mg.close_ts)
        mg = mg.assign(
            s0=start.close.to_numpy(),
            s1=end.close.to_numpy(),
            age=np.maximum(start.age.dt.total_seconds(), end.age.dt.total_seconds()),
        )
        snap = C.P.sample(
            mg,
            C.quotes("kachoio", parts=[coin]),
            "close_ts",
            {h: -TD(seconds=s) for h, s in HORIZONS.items()},
            TD("5s"),
        )
        out.append((mg, snap))
    mk = pd.concat([o[0] for o in out], ignore_index=True)
    mk["y_spot"] = (mk.s1 >= mk.s0).astype(float)
    mk["move_bp"] = (np.log(mk.s1 / mk.s0) * 1e4).abs()
    snap = pd.concat([o[1] for o in out], ignore_index=True)
    return mk, snap, cut


def agreement(mk):
    d = mk[mk.y.notna() & (mk.age == 0)]
    d = d.assign(bucket=pd.cut(d.move_bp, [0, 0.5, 1, 2, 5, 10, 1e9], include_lowest=True))
    t = d.groupby("bucket", observed=True).apply(
        lambda g: pd.Series({"n": len(g), "agree": (g.y == g.y_spot).mean()}), include_groups=False
    )
    nulls = mk[mk.y.isna()]
    print(f"Binance vs publisher outcome, decisive markets: agree {(d.y == d.y_spot).mean():.4f}")
    print(t.round(4).to_string())
    print(
        f"null-outcome markets: {len(nulls):,}; their |move| median "
        f"{nulls.move_bp.median():.2f} bp "
        f"(decisive: {d.move_bp.median():.2f} bp)"
    )


def contracts(mk, snap, y_col):
    s = snap.drop(columns=["y"], errors="ignore").merge(mk[["mkey", y_col]], on="mkey")
    s = s.rename(columns={y_col: "y"})
    s["y_token"] = np.where(s.token == 0, s.y, 1 - s.y)
    c = C.P.contracts(s, max_spread=0.05).dropna(subset=["y"])
    c["fee_paid"] = C.poly_fee(c.p_buy)
    c["ret"] = (c.y - c.p_buy - c.fee_paid) / (c.p_buy + c.fee_paid)
    c["price"] = c.p_buy
    c["cluster"] = c.close_ts.dt.strftime("%Y-%m-%d")
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["explore", "holdout"])
    ap.add_argument("--horizon")
    ap.add_argument("--lo", type=float)
    ap.add_argument("--alpha", type=float, default=0.05)
    a = ap.parse_args()
    mk, snap, cut = load()
    mk = mk.assign(y_filled=mk.y.fillna(mk.y_spot))
    print(f"kachoio: {len(mk):,} markets, split at close {cut}")
    if a.mode == "explore":
        agreement(mk[mk.split == "explore"])
        rows = []
        for y_col in ("y", "y_filled", "y_spot"):
            c = contracts(mk[mk.split == "explore"], snap, y_col)
            for h in HORIZONS:
                for lo in (0.80, 0.90, 0.95):
                    t = c[(c.horizon == h) & (c.p_buy >= lo) & (c.p_buy < 1)]
                    rows.append(
                        {"outcome": y_col, "horizon": h, "buy_if_ask_>=": lo, **C.summarise(t)}
                    )
                cal = c[(c.horizon == h) & (c.token == 0)].dropna(subset=["p_cal"])
                b = C.logreg(C.logit(cal.p_cal)[:, None], cal.y)
                rows[-1]["slope_first_token"] = b[1]
        r = pd.DataFrame(rows)
        print(
            "\nexploration: buy every contract with ask >= threshold (taker, 0.07 fee, "
            "spread <= 5c)"
        )
        print(r.to_string(index=False, float_format=lambda v: f"{v:.4f}"))
        return
    c = contracts(mk[mk.split == "holdout"], snap, "y_filled")
    t = c[(c.horizon == a.horizon) & (c.p_buy >= a.lo) & (c.p_buy < 1)]
    print(
        f"HOLDOUT horizon={a.horizon} ask>={a.lo} alpha={a.alpha}: "
        f"{C.fmt(C.summarise(t, alpha=a.alpha))}"
    )


if __name__ == "__main__":
    main()
