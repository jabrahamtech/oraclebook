"""Round 3, H3: late in the climate day, is the high already in? An empirical nowcast of
D = final high − L (L = confirmed METAR running max, rounded) from the local standard hour and how
far the latest reading has fallen below L, fitted on other days only; compared with Kalshi prices
for the same contracts. See PROTOCOL_V3.md.

Usage: python -I wx_nowcast.py explore [--series A,B]
       python -I wx_nowcast.py holdout --edge E [--min-hour H] [--alpha A]
"""

import argparse
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import kx
import wx_decided
import wxobs

D_VALUES = np.arange(-1, 9)
DROP_BINS = [-np.inf, 0.5, 1.5, 2.5, 4.5, 7.5, np.inf]


def grid():
    """One row per city-day and local hour 10..23: L, drop, final high (the settled value)."""
    rows = []
    for s, (station, offset) in wxobs.STATIONS.items():
        m = pd.read_parquet(kx.RAW / s / "markets.parquet")
        cli = m.drop_duplicates("event_ticker").set_index("event_ticker").expiration_value
        cli = pd.Series(cli.astype(float).to_numpy(), index=cli.index.map(wxobs.event_day))
        obs = wxobs.observations(station, offset)
        for day, final in cli.items():
            at = pd.Series(
                [day + pd.Timedelta(hours=h, minutes=55) for h in range(10, 24)]
            ).dt.tz_localize("UTC") - pd.Timedelta(hours=offset)
            k = wxobs.known_max(obs, at)
            ok = k.day.to_numpy() == np.datetime64(day)
            rows.append(
                pd.DataFrame(
                    {
                        "series": s,
                        "day": day,
                        "hour": np.arange(10, 24),
                        "L": np.floor(k.run_max_f + 0.5),
                        "drop": k.run_max_f - k.temp_f,
                        "final": final,
                    }
                )[ok]
            )
    g = pd.concat(rows, ignore_index=True).dropna()
    g["D"] = (g.final - g.L).clip(D_VALUES[0], D_VALUES[-1])
    return g


def features(hour, drop):
    return pd.DataFrame({"h": np.asarray(hour), "b": pd.cut(drop, DROP_BINS, labels=False)})


def fit(g, prior=1.0):
    """P(D = d | hour, drop bin), add-`prior` smoothing within each hour's pooled shape."""
    f = features(g.hour, g["drop"]).assign(D=g.D.to_numpy())
    pooled = (
        f.groupby(["h", "D"]).size().unstack(fill_value=0).reindex(columns=D_VALUES, fill_value=0)
    )
    pooled = (pooled + 0.01).div(pooled.sum(1) + 0.01 * len(D_VALUES), axis=0)
    cnt = f.groupby(["h", "b", "D"]).size().unstack(fill_value=0)
    cnt = cnt.reindex(columns=D_VALUES, fill_value=0)
    p = cnt + prior * pooled.reindex(cnt.index.get_level_values("h")).to_numpy()
    return p.div(p.sum(1), axis=0)


def prob_yes(table, t):
    """Model P(YES) per print: sum of P(D) over the finals that make the contract YES."""
    key = features(t.hour.clip(10, 23), t["drop"])
    idx = pd.MultiIndex.from_arrays([key.h, key.b])
    p = table.reindex(idx).to_numpy()
    final = t.L.to_numpy()[:, None] + D_VALUES[None, :]
    st = t.strike_type.to_numpy()[:, None]
    f, c = t.floor_strike.to_numpy()[:, None], t.cap_strike.to_numpy()[:, None]
    yes = np.where(
        st == "greater", final > f, np.where(st == "less", final < c, (final >= f) & (final <= c))
    )
    return np.nansum(p * yes, axis=1) + np.where(np.isnan(p).all(1), np.nan, 0)


def prints(series=None):
    t = wx_decided.load("pm", series)
    t = t.dropna(subset=["known"])
    off = t.series.map(lambda s: wxobs.STATIONS[s][1])
    local = t.ts + pd.to_timedelta(off, unit="h")
    t["hour"] = local.dt.hour
    k = []
    for s, (station, offset) in wxobs.STATIONS.items():
        sub = t[t.series == s]
        if len(sub):
            k.append(wxobs.known_max(wxobs.observations(station, offset), sub.ts).temp_f)
    t["drop"] = t.known - pd.concat(k).reindex(t.index)
    t["day"] = t.event_ticker.map(wxobs.event_day)
    return t


def scored(t, g, folds=5):
    """Out-of-sample model probability per print: fit on other days (by day-of-period folds)."""
    days = np.sort(g.day.unique())
    fold = dict(zip(days, np.arange(len(days)) % folds, strict=True))
    out = pd.Series(np.nan, index=t.index)
    tf = t.day.map(fold)
    for k in range(folds):
        table = fit(g[g.day.map(fold) != k])
        sel = tf == k
        out[sel] = prob_yes(table, t[sel])
    return out


def takers(t, edge, min_hour=13):
    rows = []
    for side in ("yes", "no"):
        p = t.yes_price if side == "yes" else 1 - t.yes_price
        q = t.q if side == "yes" else 1 - t.q
        fee = t.fee_taker * p * (1 - p)
        sel = (t.taker_side == side) & (q - p - fee > edge) & (t.hour >= min_hour)
        d = t[sel]
        p, fee = p[sel], fee[sel]
        y = d.y if side == "yes" else 1 - d.y
        rows.append(d.assign(cost=(p + fee) * d["count"], pnl=(y - p - fee) * d["count"]))
    return pd.concat(rows)


def explore(series):
    g = grid()
    ge = g[g.day < kx.SPLIT.tz_localize(None)]
    print(f"grid: {len(ge):,} city-day-hours (exploration)")
    print(
        ge.assign(b=pd.cut(ge["drop"], DROP_BINS))
        .groupby(["hour"])
        .D.apply(lambda d: f"P(D=0) {(d == 0).mean():.2f}  P(D>=1) {(d >= 1).mean():.2f}")
        .to_string()
    )
    t = prints(series)
    t = t[t.split == "explore"]
    t["q"] = scored(t, ge)
    t = t.dropna(subset=["q"])
    yes_p = t.yes_price
    print(
        f"prints {len(t):,}: Brier market {((yes_p - t.y) ** 2).mean():.4f}, "
        f"model {((t.q - t.y) ** 2).mean():.4f}"
    )
    for h0 in (13, 16, 19):
        sub = t[t.hour >= h0]
        print(
            f"  hour>={h0}: n={len(sub):,} Brier market {((sub.yes_price - sub.y) ** 2).mean():.4f}"
            f", model {((sub.q - sub.y) ** 2).mean():.4f}"
        )
    for edge in (0.02, 0.05, 0.10, 0.20):
        for h0 in (13, 16):
            f = takers(t, edge, h0)
            print(f"edge {edge:.2f} hour>={h0}: {kx.fmt(kx.ratio_boot(f, 'event_ticker'))}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["explore", "holdout"])
    ap.add_argument("--series")
    ap.add_argument("--edge", type=float)
    ap.add_argument("--min-hour", type=int, default=13)
    ap.add_argument("--alpha", type=float, default=0.05)
    a = ap.parse_args()
    series = a.series.split(",") if a.series else None
    if a.mode == "explore":
        explore(series)
        return
    g = grid()
    table = fit(g[g.day < kx.SPLIT.tz_localize(None)])
    t = prints(series)
    t = t[t.split == "holdout"]
    t["q"] = prob_yes(table, t)
    f = takers(t.dropna(subset=["q"]), a.edge, a.min_hour)
    print(
        f"HOLDOUT nowcast taker, edge {a.edge}, hour>={a.min_hour}: "
        f"{kx.fmt(kx.ratio_boot(f, 'event_ticker', alpha=a.alpha))}"
    )


if __name__ == "__main__":
    main()
