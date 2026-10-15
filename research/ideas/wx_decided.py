"""Round 3, H1: do Kalshi daily-high contracts trade below their settled value after the METARs
have decided them? See PROTOCOL_V3.md.

For every trade print, the decided side (if any) given the confirmed METAR maximum known 10
minutes before the print and a margin delta (°F). Taker view: buy that side at a print where
another taker bought it (price + taker fee). Maker view (M1, a population measure): the resting
orders that bought the decided side when a taker sold it (price + maker fee).

Usage: python -I wx_decided.py explore [--tape pm|am|low] [--series A,B]
       python -I wx_decided.py holdout --delta D [--tape pm|low] [--maker] [--min-price P]
                                       [--max-price Q] [--alpha A]
"""

import argparse
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import kx
import wxobs

TAPES = {
    "am": kx.RAW,
    "pm": wxobs.RAW / "weather_pm" / "kalshi_api",
    "low": wxobs.RAW / "weather_low" / "kalshi_api",
    "d1": wxobs.RAW / "weather_d1" / "kalshi_api",
}


def load(tape, series=None):
    out = []
    for s, (station, offset) in wxobs.STATIONS.items():
        if series and s not in series:
            continue
        low = tape == "low"
        _, t = kx.load([wxobs.low_series(s) if low else s], root=TAPES[tape])
        # Lows use spot readings only (see wxobs.observations); highs keep the 6-hour maxima.
        obs = wxobs.observations(station, offset, "min" if low else "max", six_hour=not low)
        k = wxobs.known_max(obs, t.ts)
        day = t.event_ticker.map(wxobs.event_day)
        known = np.where(k.day.to_numpy() == day.to_numpy(), k.run_max_f, np.nan)
        # For lows the observations are negated: known is the running minimum.
        t = t.assign(known=-known if low else known, low=low)
        out.append(t)
    t = pd.concat(out, ignore_index=True)
    t["L"] = np.floor(t.known + 0.5)
    t["left_h"] = (t.close_time - t.ts).dt.total_seconds() / 3600
    return t


def decided(t, delta):
    """'yes', 'no' or None per print. Daily high: L is the running max (a lower bound of the
    final); daily low: L is the running min (an upper bound)."""
    L, st, f, c = t.L, t.strike_type, t.floor_strike, t.cap_strike
    hi_yes = (st == "greater") & (L >= f + 1 + delta)
    hi_no = ((st == "less") & (L >= c + delta)) | ((st == "between") & (L >= c + 1 + delta))
    lo_yes = (st == "less") & (L <= c - 1 - delta)
    lo_no = ((st == "greater") & (L <= f - delta)) | ((st == "between") & (L <= f - 1 - delta))
    yes = np.where(t.low, lo_yes, hi_yes)
    no = np.where(t.low, lo_no, hi_no)
    return pd.Series(np.select([yes, no], ["yes", "no"], None), index=t.index)


def entries(t, delta, lo=0.0, hi=1.0, maker=False):
    d = t.assign(side=decided(t, delta))
    d = d[d.side.notna() & ((d.taker_side != d.side) if maker else (d.taker_side == d.side))]
    p = np.where(d.side == "yes", d.yes_price, 1 - d.yes_price)
    won = np.where(d.side == "yes", d.y, 1 - d.y)
    d = d.assign(p=p, won=won)
    d = d[(d.p >= lo) & (d.p <= hi)]
    fee = (d.fee_maker if maker else d.fee_taker) * d.p * (1 - d.p)
    return d.assign(cost=(d.p + fee) * d["count"], pnl=(d.won - d.p - fee) * d["count"])


def explore(t):
    e = t[t.split == "explore"]
    print(f"prints {len(e):,}, with a known max {e.known.notna().mean():.0%}")
    for delta in (0, 1, 2, 3):
        side = decided(e, delta)
        dd = e[side.notna()].assign(side=side)
        won = np.where(dd.side == "yes", dd.y, 1 - dd.y)
        f = entries(e, delta)
        print(
            f"delta {delta}: decided prints {len(dd):,} in {dd.ticker.nunique()} markets, "
            f"decided side lost {1 - won.mean():.3%} of prints / "
            f"{1 - dd.assign(w=won).groupby('ticker').w.min().mean():.2%} of markets"
        )
        print(
            f"   taker buys of decided side: {len(f):,}, mean price {f.p.mean():.3f}, "
            f"{kx.fmt(kx.ratio_boot(f, 'event_ticker'))}"
        )
        for lo, hi in ((0, 0.9), (0.9, 0.97), (0.97, 0.99), (0.99, 1.0)):
            g = f[(f.p >= lo) & (f.p < hi + 1e-9)]
            print(f"     price {lo:.2f}-{hi:.2f}: {kx.fmt(kx.ratio_boot(g, 'event_ticker'))}")
        g = entries(e, delta, maker=True)
        days = e.event_ticker.nunique()
        print(
            f"   makers buying the decided side: {g['count'].sum():,.0f} contracts, "
            f"mean price {g.p.mean():.3f}, ${g.pnl.sum() / days:,.1f} per city-day, "
            f"{kx.fmt(kx.ratio_boot(g, 'event_ticker'))}"
        )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["explore", "holdout"])
    ap.add_argument("--tape", choices=list(TAPES), default="pm")
    ap.add_argument("--delta", type=int)
    ap.add_argument("--maker", action="store_true")
    ap.add_argument("--min-price", type=float, default=0.0)
    ap.add_argument("--max-price", type=float, default=1.0)
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--series", help="comma-separated subset (default: all 20 cities)")
    a = ap.parse_args()
    t = load(a.tape, a.series.split(",") if a.series else None)
    if a.mode == "explore":
        explore(t)
        return
    f = entries(t[t.split == "holdout"], a.delta, a.min_price, a.max_price, a.maker)
    who = "maker (population)" if a.maker else "taker"
    print(
        f"HOLDOUT decided-side {who}, delta {a.delta}, price {a.min_price}-{a.max_price}: "
        f"{kx.fmt(kx.ratio_boot(f, 'event_ticker', alpha=a.alpha))}"
    )


if __name__ == "__main__":
    main()
