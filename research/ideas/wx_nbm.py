"""Round 3, H4/H5: the National Blend of Models (NBS daytime max `txn` and spread `xnd`, latest
run available 90 minutes after its run time) as a probability for each daily-high contract,
compared with Kalshi prices on the morning tape (H4, 06:00–10:00 local) or the day-before tape
(H5). The normal's bias and spread scale are fitted on other days only (date folds within
exploration; all of exploration for the holdout).

Usage: python -I wx_nbm.py explore [--tape am|d1]
       python -I wx_nbm.py holdout --edge E [--tape am|d1] [--alpha A]
"""

import argparse
import math
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import kx
import wx_decided
import wxobs

AVAIL = pd.Timedelta(minutes=90)
PHI = np.vectorize(lambda z: 0.5 * (1 + math.erf(z / math.sqrt(2))))


def nbm(station):
    d = pd.read_csv(
        wxobs.RAW / "iem_nbm" / f"K{station}.csv", usecols=["runtime", "ftime", "txn", "xnd"]
    )
    d = d.dropna(subset=["txn", "xnd"])
    d["runtime"] = pd.to_datetime(d.runtime, utc=True)
    d["ftime"] = pd.to_datetime(d.ftime, utc=True)
    # Daytime maximum for day D is the 00Z column of D + 1.
    d = d[d.ftime.dt.hour == 0]
    d["day"] = (d.ftime - pd.Timedelta(days=1)).dt.tz_localize(None).dt.floor("D")
    d["avail"] = d.runtime + AVAIL
    return d[["day", "avail", "txn", "xnd"]].sort_values("avail")


def attach(t):
    out = []
    for s, (station, _) in wxobs.STATIONS.items():
        sub = t[t.series == s]
        if sub.empty:
            continue
        f = nbm(station)
        sub = sub.sort_values("ts").assign(day=sub.event_ticker.map(wxobs.event_day))
        r = pd.merge_asof(sub, f, left_on="ts", right_on="avail", by="day", direction="backward")
        out.append(r)
    return pd.concat(out, ignore_index=True).dropna(subset=["txn"])


def days_table(at_hour=6):
    """Per city-day: the settled high and the last NBM run available by `at_hour` local standard
    time on the day (negative: the day before, e.g. -12 = 12:00 on D - 1)."""
    rows = []
    for s, (station, offset) in wxobs.STATIONS.items():
        m = pd.read_parquet(kx.RAW / s / "markets.parquet")
        cli = m.drop_duplicates("event_ticker").set_index("event_ticker").expiration_value
        cli = pd.DataFrame(
            {"final": cli.astype(float).to_numpy(), "day": cli.index.map(wxobs.event_day)}
        )
        cli["at"] = (cli.day + pd.Timedelta(hours=at_hour - offset)).dt.tz_localize("UTC")
        r = pd.merge_asof(
            cli.sort_values("at"),
            nbm(station),
            left_on="at",
            right_on="avail",
            by="day",
            direction="backward",
        )
        rows.append(r.assign(series=s))
    return pd.concat(rows, ignore_index=True).dropna(subset=["txn"])


def calib(days):
    z = (days.final - days.txn) / days.xnd
    return z.mean(), z.std()


def prob_yes(t, bias, scale):
    mu = t.txn + bias * t.xnd
    sd = scale * t.xnd

    def above(k):  # P(high >= k), whole degrees
        return 1 - PHI((k - 0.5 - mu) / sd)

    st = t.strike_type
    return np.where(
        st == "greater",
        above(t.floor_strike + 1),
        np.where(
            st == "less", 1 - above(t.cap_strike), above(t.floor_strike) - above(t.cap_strike + 1)
        ),
    )


def scored(t, days, folds=5):
    ud = np.sort(days.day.unique())
    fold = dict(zip(ud, np.arange(len(ud)) % folds, strict=True))
    q = pd.Series(np.nan, index=t.index)
    tf = t.day.map(fold)
    for k in range(folds):
        b, s = calib(days[days.day.map(fold) != k])
        sel = tf == k
        q[sel] = prob_yes(t[sel], b, s)
    return q


def takers(t, edge):
    rows = []
    for side in ("yes", "no"):
        p = t.yes_price if side == "yes" else 1 - t.yes_price
        q = t.q if side == "yes" else 1 - t.q
        fee = t.fee_taker * p * (1 - p)
        sel = (t.taker_side == side) & (q - p - fee > edge)
        d, p, fee = t[sel], p[sel], fee[sel]
        y = d.y if side == "yes" else 1 - d.y
        rows.append(d.assign(cost=(p + fee) * d["count"], pnl=(y - p - fee) * d["count"]))
    return pd.concat(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["explore", "holdout"])
    ap.add_argument("--edge", type=float)
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--tape", choices=["am", "d1"], default="am")
    a = ap.parse_args()
    days = days_table(6 if a.tape == "am" else -12)
    split = kx.SPLIT.tz_localize(None)
    de = days[days.day < split]
    t = attach(wx_decided.load(a.tape))
    if a.mode == "holdout":
        b, s = calib(de)
        t = t[t.split == "holdout"]
        t["q"] = prob_yes(t, b, s)
        f = takers(t, a.edge)
        print(
            f"HOLDOUT NBM taker ({a.tape}), edge {a.edge}: "
            f"{kx.fmt(kx.ratio_boot(f, 'event_ticker', alpha=a.alpha))}"
        )
        return
    b, s = calib(de)
    print(
        f"city-days {len(de)}: z = (high - txn)/xnd mean {b:+.2f}, sd {s:.2f}; "
        f"MAE txn {np.abs(de.final - de.txn).mean():.2f} F"
    )
    t = t[t.split == "explore"]
    t["q"] = scored(t, de)
    print(
        f"prints {len(t):,}: Brier market {((t.yes_price - t.y) ** 2).mean():.4f}, "
        f"NBM {((t.q - t.y) ** 2).mean():.4f}, "
        f"blend {((0.5 * t.q + 0.5 * t.yes_price - t.y) ** 2).mean():.4f}"
    )
    for edge in (0.02, 0.05, 0.10, 0.20):
        f = takers(t, edge)
        print(f"edge {edge:.2f}: {kx.fmt(kx.ratio_boot(f, 'event_ticker'))}")
    f = takers(t, 0.05)
    for s_ in sorted(f.series.unique()):
        print(f"   {s_}: {kx.fmt(kx.ratio_boot(f[f.series == s_], 'event_ticker'))}")


if __name__ == "__main__":
    main()
