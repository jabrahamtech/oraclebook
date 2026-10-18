"""Idea P1 (PROTOCOL_V6.md): pre-open lean in 5-minute crypto Up/Down. Before a window opens its
strike is not set, so P(Up) is 0.5 whatever has happened; when the pre-open Up mid leans at least
d away from 0.5, buy the side quoted below 0.5 at its own book's ask (taker, 0.07 fee) and hold to
resolution. Outcomes: the publisher's, with its close calls rebuilt from Binance (as in
crypto_favourites.py).

Usage: python -I pm_preopen.py explore
       python -I pm_preopen.py holdout --tau T --lean D --alpha 0.01   (frozen rule, run once)
"""

import argparse
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common as C

TD = pd.Timedelta
TAUS = {"60s": 60, "30s": 30, "10s": 10}
LEANS = (0.02, 0.03, 0.05)
MAX_SPREAD = 0.05
MIN_MARKETS = 200


def load():
    import crypto_favourites as CF

    mk, _snap, cut = CF.load()
    mk = mk.assign(y=mk.y.fillna(mk.y_spot))
    snaps = []
    for coin, mg in mk.groupby("coin"):
        snaps.append(
            C.P.sample(
                mg,
                C.quotes("kachoio", parts=[coin], columns=["mkey", "token", "ts", "bid", "ask"]),
                "open_ts",
                {h: -TD(seconds=s) for h, s in TAUS.items()},
                TD("5s"),
            )
        )
    s = pd.concat(snaps, ignore_index=True)
    s = s[(s.bid > 0) & (s.ask < 1) & (s.bid <= s.ask) & (s.ask - s.bid <= MAX_SPREAD)]
    up = s[s.token == 0][["mkey", "horizon", "bid", "ask"]].rename(
        columns={"bid": "up_bid", "ask": "up_ask"}
    )
    dn = s[s.token == 1][["mkey", "horizon", "ask"]].rename(columns={"ask": "down_ask"})
    d = up.merge(dn, on=["mkey", "horizon"], how="left").merge(
        mk[["mkey", "coin", "close_ts", "split", "y"]], on="mkey"
    )
    d["mid"] = (d.up_bid + d.up_ask) / 2
    d["cluster"] = d.close_ts.dt.strftime("%Y-%m-%d")
    return d[d.y.notna()], cut


def trades(d, lean):
    x = d[(d.mid - 0.5).abs() >= lean]
    buy_up = x.mid < 0.5
    price = np.where(buy_up, x.up_ask, x.down_ask)
    t = x.assign(price=price, y=np.where(buy_up, x.y, 1 - x.y)).dropna(subset=["price"])
    f = C.poly_fee(t.price)
    return t.assign(ret=(t.y - t.price - f) / (t.price + f))


def explore(d):
    e = d[d.split == "explore"]
    usable = e.groupby("horizon").mkey.nunique()
    print("P1 usable pre-open quotes: " + ", ".join(f"{h} {n}" for h, n in usable.items()))
    if usable.max() < MIN_MARKETS:
        print(f"P1 not testable: < {MIN_MARKETS} markets at every tau")
        return
    for h, g in e.groupby("horizon"):
        q = g.mid.quantile([0.01, 0.5, 0.99]).round(3).tolist()
        print(f"  tau={h} mid p1/p50/p99 {q}")
        for lean in LEANS:
            print(f"    lean>={lean}: {C.fmt(C.summarise(trades(g, lean)))}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["explore", "holdout"])
    ap.add_argument("--tau", choices=list(TAUS))
    ap.add_argument("--lean", type=float)
    ap.add_argument("--alpha", type=float, default=0.05)
    a = ap.parse_args()
    d, cut = load()
    print(f"kachoio: {d.mkey.nunique():,} markets with pre-open quotes, split at close {cut}")
    if a.mode == "explore":
        explore(d)
        return
    h = d[(d.split == "holdout") & (d.horizon == a.tau)]
    print(
        f"HOLDOUT tau={a.tau} lean>={a.lean} alpha={a.alpha}: "
        f"{C.fmt(C.summarise(trades(h, a.lean), alpha=a.alpha))}"
    )


if __name__ == "__main__":
    main()
