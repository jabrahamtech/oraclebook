"""Round 2, idea F: informed order flow. At a decision time t per market, imbalance = (taker
dollars buying YES - taker dollars buying NO) / total over a look-back window; price = the last
trade's YES price before t. Does imbalance predict the outcome beyond the price (fit on the first
half of exploration, score log loss on the second half)? Rule: follow the flow — buy the side the
takers were buying, at that side's most recent taker-buy print, when |imbalance| > k and at least
`min_dollars` traded in the window; taker fee applies.

Groups (decision time t, look-back window):
  weather  t = 08:00 local on day D (close - 17 h), window 2 h
  sports   t = occurrence - 5 h, window 7 h (the fetched 12 -> 4 h slice)
  crypto   t = close - 15 min (hourly above/below), window 30 min
  crypto15 t = close - 5 min (15-minute up/down), window 10 min

Usage: python -I kx_flow.py <weather|sports|crypto> explore
       python -I kx_flow.py <group> holdout --k X --min-dollars D
"""

import argparse
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common as C
import kx
import kx_makers as KM
import weather as W

GROUPS = {
    "weather": (list(W.CITIES), "close", 17, 2),
    "sports": (KM.SPORTS, "occ", 5, 7),
    "crypto": (["KXBTCD", "KXETHD"], "close", 0.25, 0.5),
    "crypto15": (["KXBTC15M", "KXETH15M"], "close", 5 / 60, 10 / 60),
}


def load(group):
    series, ref, before_h, window_h = GROUPS[group]
    m, t = kx.load([s for s in series if s in kx.available()])
    base = t.occurrence_datetime.fillna(t.close_time) if ref == "occ" else t.close_time
    t = t.assign(t_dec=base - pd.Timedelta(hours=before_h))
    t = t[(t.ts < t.t_dec) & (t.ts >= t.t_dec - pd.Timedelta(hours=window_h))]
    t = kx.sides(t)
    t["usd"] = t.p_taker * t["count"]
    t["signed"] = np.where(t.taker_side == "yes", t.usd, -t.usd)
    g = t.sort_values("ts").groupby("ticker")
    f = pd.DataFrame(
        {
            "flow": g.signed.sum(),
            "usd": g.usd.sum(),
            "last_yes": g.yes_price.last(),
            "n_trades": g.size(),
        }
    )
    last_buy = t.sort_values("ts").groupby(["ticker", "taker_side"]).yes_price.last().unstack()
    last_buy = last_buy.reindex(columns=["yes", "no"])
    f["ask_yes"], f["ask_no"] = last_buy["yes"], 1 - last_buy["no"]
    f["imb"] = f.flow / f.usd
    f = f.join(
        m.set_index("ticker")[["y", "split", "close_time", "event_ticker", "fee_taker", "series"]]
    )
    return f.reset_index()


def trades_for(f, k, min_dollars, fade=False):
    """follow: buy the side takers were buying; fade: buy the side they were selling."""
    sel = f[(f.usd >= min_dollars) & (f.imb.abs() > k)]
    yes = (sel.imb > 0) != fade
    price = np.where(yes, sel.ask_yes, sel.ask_no)
    y = np.where(yes, sel.y, 1 - sel.y)
    d = sel.assign(price=price, yy=y)
    d = d[d.price.between(0.01, 0.99)]
    fee = d.fee_taker * d.price * (1 - d.price)
    return d.assign(count=1.0, cost=d.price + fee, pnl=d.yy - d.price - fee)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("group", choices=list(GROUPS))
    ap.add_argument("mode", choices=["explore", "holdout"])
    ap.add_argument("--k", type=float)
    ap.add_argument("--min-dollars", type=float, default=100)
    ap.add_argument("--fade", action="store_true")
    ap.add_argument("--alpha", type=float, default=0.05)
    a = ap.parse_args()
    f = load(a.group)
    if a.mode == "holdout":
        tr = trades_for(f[f.split == "holdout"], a.k, a.min_dollars, a.fade)
        print(
            f"HOLDOUT flow {a.group} k={a.k} min$={a.min_dollars} fade={a.fade}: "
            f"{kx.fmt(kx.ratio_boot(tr, 'event_ticker', alpha=a.alpha))}"
        )
        return
    e = f[(f.split == "explore") & f.last_yes.between(0.02, 0.98) & (f.usd >= 100)]
    half = e.close_time.quantile(0.5)
    x, z = e[e.close_time < half], e[e.close_time >= half]
    b0 = C.logreg(C.logit(x.last_yes)[:, None], x.y)
    b1 = C.logreg(np.column_stack([C.logit(x.last_yes), x.imb]), x.y)
    p0 = C.predict(b0, C.logit(z.last_yes)[:, None])
    p1 = C.predict(b1, np.column_stack([C.logit(z.last_yes), z.imb]))
    print(
        f"{a.group}: markets {len(e):,}; imbalance coef {b1[2]:+.3f}; out-of-sample log-loss gain "
        f"{C.logloss(p0, z.y) - C.logloss(p1, z.y):+.4f} (price-only {C.logloss(p0, z.y):.4f})"
    )
    for md in (100, 1000):
        for k in (0.2, 0.4, 0.6, 0.8):
            tr = trades_for(f[f.split == "explore"], k, md)
            fd = trades_for(f[f.split == "explore"], k, md, fade=True)
            print(f"  fade   flow |imb|>{k} min$ {md}: {kx.fmt(kx.ratio_boot(fd, 'event_ticker'))}")
            print(f"  follow flow |imb|>{k} min$ {md}: {kx.fmt(kx.ratio_boot(tr, 'event_ticker'))}")


if __name__ == "__main__":
    main()
