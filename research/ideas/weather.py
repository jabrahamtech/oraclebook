"""Round 2, idea W: Kalshi daily-high temperature markets vs a forecast. For city c and day D,
the forecast high is the max over D's local hours of Open-Meteo's archived "previous day 1"
hourly forecast (each value issued 24 h before its hour, so all of it is known before D starts).
On exploration days, fit actual = a + b * forecast + N(0, sigma) per city (actual = Kalshi's
settlement value). A market "high in [lo, hi]" then has P = Phi((hi + 0.5 - mu) / sigma) -
Phi((lo - 0.5 - mu) / sigma). Decision at 08:00 local on D: the executable price of a side is the
last taker-buy trade price of that side in the previous 60 minutes (a taker buy prints at the
ask). Buy the side whose model probability beats that price plus the taker fee by > theta; hold
to settlement.

Usage: python -I weather.py fetch            (Open-Meteo archive -> data/raw/openmeteo)
       python -I weather.py explore
       python -I weather.py holdout --theta X --hour H
"""

import argparse
import json
import math
import pathlib
import subprocess
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import kx

OM = pathlib.Path(__file__).resolve().parents[1] / "data" / "raw" / "openmeteo"
URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
# Settlement stations (NWS climate sites named in each series' rules) and local time zones.
CITIES = {
    "KXHIGHNY": (40.7789, -73.9692, "America/New_York"),
    "KXHIGHCHI": (41.7868, -87.7522, "America/Chicago"),
    "KXHIGHMIA": (25.7959, -80.2870, "America/New_York"),
    "KXHIGHLAX": (33.9425, -118.4081, "America/Los_Angeles"),
    "KXHIGHAUS": (30.1945, -97.6699, "America/Chicago"),
    "KXHIGHDEN": (39.8561, -104.6737, "America/Denver"),
    "KXHIGHPHIL": (39.8729, -75.2437, "America/New_York"),
    "KXHIGHTSFO": (37.6190, -122.3749, "America/Los_Angeles"),
    "KXHIGHTATL": (33.6407, -84.4277, "America/New_York"),
    "KXHIGHTHOU": (29.6454, -95.2789, "America/Chicago"),
    "KXHIGHTDC": (38.8512, -77.0402, "America/New_York"),
    "KXHIGHTSEA": (47.4502, -122.3088, "America/Los_Angeles"),
    "KXHIGHTLV": (36.0840, -115.1537, "America/Los_Angeles"),
    "KXHIGHTBOS": (42.3656, -71.0096, "America/New_York"),
    "KXHIGHTPHX": (33.4342, -112.0116, "America/Phoenix"),
    "KXHIGHTDAL": (32.8998, -97.0403, "America/Chicago"),
    "KXHIGHTMIN": (44.8848, -93.2223, "America/Chicago"),
    "KXHIGHTNOLA": (29.9934, -90.2580, "America/Chicago"),
    "KXHIGHTOKC": (35.3931, -97.6007, "America/Chicago"),
    "KXHIGHTSATX": (29.5337, -98.4698, "America/Chicago"),
}


def fetch():
    OM.mkdir(parents=True, exist_ok=True)
    for s, (lat, lon, tz) in CITIES.items():
        q = (
            f"{URL}?latitude={lat}&longitude={lon}&hourly=temperature_2m_previous_day1,"
            f"temperature_2m_previous_day2&temperature_unit=fahrenheit&timezone={tz}"
            f"&start_date=2026-08-10&end_date=2026-10-15"
        )
        r = subprocess.run(
            ["curl", "-s", "-m", "60", q], capture_output=True, text=True, check=False
        )
        d = json.loads(r.stdout)
        if "hourly" not in d:
            print(s, "failed", str(d)[:200])
            continue
        (OM / f"{s}.json").write_text(json.dumps(d))
        print(s, "ok", len(d["hourly"]["time"]))


def forecasts():
    rows = []
    for s in CITIES:
        f = OM / f"{s}.json"
        if not f.exists():
            continue
        h = json.loads(f.read_text())["hourly"]
        d = pd.DataFrame(
            {
                "time": pd.to_datetime(h["time"]),
                "f1": h["temperature_2m_previous_day1"],
                "f2": h["temperature_2m_previous_day2"],
            }
        )
        d["date"] = d.time.dt.date
        g = d.groupby("date")[["f1", "f2"]].max().reset_index().assign(series=s)
        rows.append(g)
    return pd.concat(rows, ignore_index=True)


def market_days(m):
    """Day D and the [lo, hi] integer range each market pays on."""
    m = m.copy()
    m["date"] = pd.to_datetime(m.event_ticker.str.split("-").str[1], format="%y%b%d").dt.date
    lo = np.where(
        m.strike_type == "greater",
        m.floor_strike + 1,
        np.where(m.strike_type == "between", m.floor_strike, -np.inf),
    )
    hi = np.where(
        m.strike_type == "less",
        m.cap_strike - 1,
        np.where(m.strike_type == "between", m.cap_strike, np.inf),
    )
    m["lo"], m["hi"] = lo, hi
    m["actual"] = pd.to_numeric(m.expiration_value, errors="coerce")
    return m


def phi(x):
    return 0.5 * (1 + np.vectorize(math.erf)(np.asarray(x, float) / math.sqrt(2)))


def fit(days):
    """Per-city a, b, sigma on exploration days (one row per city-day)."""
    out = {}
    for s, g in days.groupby("series"):
        g = g.dropna(subset=["actual", "f1"])
        if len(g) < 15:
            continue
        b, a = np.polyfit(g.f1, g.actual, 1)
        resid = g.actual - (a + b * g.f1)
        out[s] = (a, b, max(resid.std(ddof=2), 1.0))
    return out


def model_probs(m, params):
    p = []
    for r in m.itertuples():
        a, b, sd = params.get(r.series, (np.nan, np.nan, np.nan))
        mu = a + b * r.f1
        p.append(float(phi((r.hi + 0.5 - mu) / sd) - phi((r.lo - 0.5 - mu) / sd)))
    return m.assign(p_model=p)


def prices_at(m, t, hour):
    """Last taker-buy price of YES and of NO within the hour before `hour`:00 local on day D."""
    tz = {s: CITIES[s][2] for s in CITIES}
    out = []
    for s, g in m.groupby("series"):
        dec = pd.to_datetime(g.date.astype(str)) + pd.Timedelta(hours=hour)
        dec = dec.dt.tz_localize(tz[s]).dt.tz_convert("UTC")
        g = g.assign(t_dec=dec.to_numpy())
        tt = t[t.ticker.isin(g.ticker)].merge(g[["ticker", "t_dec"]], on="ticker")
        tt = tt[(tt.ts <= tt.t_dec) & (tt.ts > tt.t_dec - pd.Timedelta(hours=1))]
        last = tt.sort_values("ts").groupby(["ticker", "taker_side"]).yes_price.last().unstack()
        last = last.reindex(columns=["yes", "no"])
        g = g.merge(
            last.rename(columns={"yes": "ask_yes", "no": "yes_px_of_no_buy"}),
            left_on="ticker",
            right_index=True,
            how="left",
        )
        g["ask_no"] = 1 - g.yes_px_of_no_buy
        out.append(g)
    return pd.concat(out, ignore_index=True)


def trades_for(d, theta):
    rows = []
    for side, ask, pw, yv in (
        ("yes", d.ask_yes, d.p_model, d.y),
        ("no", d.ask_no, 1 - d.p_model, 1 - d.y),
    ):
        fee = d.fee_taker * ask * (1 - ask)
        edge = pw - ask - fee
        ok = ask.between(0.01, 0.99) & (edge > theta)
        t = d[ok].assign(side=side, price=ask[ok], fee=fee[ok], y=yv[ok], edge=edge[ok])
        rows.append(t)
    t = pd.concat(rows).sort_values("edge").groupby("ticker").tail(1)
    return t.assign(
        count=1.0,
        cost=t.price + t.fee,
        pnl=t.y - t.price - t.fee,
        cluster=t.series + "|" + t.date.astype(str),
    )


def load(hour):
    m, t = kx.load([s for s in CITIES if s in kx.available()])
    m = market_days(m).merge(forecasts(), on=["series", "date"], how="left")
    days = m.dropna(subset=["actual"]).drop_duplicates(["series", "date"])
    ex_days = days[days.split == "explore"]
    params = fit(ex_days)
    # Holdout markets use the model fitted on all exploration days; exploration markets use a
    # leave-one-day-out fit so exploration results are out of sample too.
    parts = [model_probs(m[m.split == "holdout"], params)]
    for date, g in m[m.split == "explore"].groupby("date"):
        parts.append(model_probs(g, fit(ex_days[ex_days.date != date])))
    m = pd.concat(parts, ignore_index=True)
    d = prices_at(m, t, hour)
    return d, days, params


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["fetch", "explore", "holdout"])
    ap.add_argument("--theta", type=float)
    ap.add_argument("--hour", type=int, default=8)
    ap.add_argument("--alpha", type=float, default=0.05)
    a = ap.parse_args()
    if a.mode == "fetch":
        fetch()
        return
    d, days, params = load(a.hour)
    if a.mode == "holdout":
        tr = trades_for(d[d.split == "holdout"], a.theta)
        print(
            f"HOLDOUT weather hour={a.hour} theta={a.theta}: "
            f"{kx.fmt(kx.ratio_boot(tr, 'cluster', alpha=a.alpha))}"
        )
        return
    e = days[days.split == "explore"]
    print(
        f"exploration city-days {len(e)}; forecast error sd by city:",
        {k[6:]: round(v[2], 2) for k, v in params.items()},
    )
    x = d[(d.split == "explore") & d.p_model.notna()]
    both = x.dropna(subset=["ask_yes", "ask_no"])
    mid = (both.ask_yes + (1 - both.ask_no)) / 2
    print(
        f"brier market mid {((mid - both.y) ** 2).mean():.4f} vs model "
        f"{((both.p_model - both.y) ** 2).mean():.4f} on {len(both)} markets with both sides traded"
    )
    for hour in (a.hour,):
        for theta in (0.0, 0.03, 0.05, 0.10, 0.15):
            tr = trades_for(x, theta)
            print(f"hour {hour} theta {theta:.2f}: {kx.fmt(kx.ratio_boot(tr, 'cluster'))}")


if __name__ == "__main__":
    main()


def maker_sim(split, post_hour=7, end_hour=10, lo=0.10, hi=0.90):
    """Queue-aware maker check: at post_hour local, the YES bid / ask are the last prints of
    taker-sells / taker-buys of YES in the previous hour. Rest a bid at the bid (buys YES) and an
    offer at the ask (buys NO at 1 - ask) on markets whose mid is in [lo, hi]; a quote fills only
    if a later trade before end_hour prints through it. Held to settlement; maker fee per series."""
    m, t = kx.load([s for s in CITIES if s in kx.available()])
    m = market_days(m[m.split == split])
    t = t[t.ticker.isin(m.ticker)]
    out = []
    for s, g in m.groupby("series"):
        tz = CITIES[s][2]
        base = pd.to_datetime(g.date.astype(str)).dt.tz_localize(tz)
        g = g.assign(
            t_post=(base + pd.Timedelta(hours=post_hour)).dt.tz_convert("UTC").to_numpy(),
            t_end=(base + pd.Timedelta(hours=end_hour)).dt.tz_convert("UTC").to_numpy(),
        )
        tt = t[t.series == s].merge(g[["ticker", "t_post", "t_end"]], on="ticker")
        before = tt[(tt.ts <= tt.t_post) & (tt.ts > tt.t_post - pd.Timedelta(hours=1))]
        q = before.sort_values("ts").groupby(["ticker", "taker_side"]).yes_price.last().unstack()
        q = q.reindex(columns=["yes", "no"]).rename(columns={"yes": "ask", "no": "bid"})
        after = tt[(tt.ts > tt.t_post) & (tt.ts <= tt.t_end)]
        ext = after.groupby("ticker").yes_price.agg(lo_after="min", hi_after="max")
        g = g.merge(q, left_on="ticker", right_index=True).merge(
            ext, left_on="ticker", right_index=True
        )
        g = g[(g.bid < g.ask) & ((g.bid + g.ask) / 2).between(lo, hi)]
        out.append(g)
    g = pd.concat(out, ignore_index=True)
    rows = []
    for side in ("yes", "no"):
        if side == "yes":
            f = g[g.lo_after < g.bid].assign(price=lambda d: d.bid, yy=lambda d: d.y)
        else:
            f = g[g.hi_after > g.ask].assign(price=lambda d: 1 - d.ask, yy=lambda d: 1 - d.y)
        fee = f.fee_maker * f.price * (1 - f.price)
        rows.append(f.assign(side=side, count=1.0, cost=f.price + fee, pnl=f.yy - f.price - fee))
    fills = pd.concat(rows)
    fills["cluster"] = fills.series + "|" + fills.date.astype(str)
    return len(g), fills
