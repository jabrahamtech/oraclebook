"""GDPNow vs Kalshi real-GDP brackets (advance estimate, annualised, published to 0.1 pp), the
secondary test of PROTOCOL_MACRO.md: same information time (close − 24 h, nowcast dated the day
before), same normal model, errors calibrated on GDPNow's own track record for quarters
2014Q1 → 2021Q4 (before any Kalshi GDP market).

Usage: python -I macro_gdp.py explore [--window H]
       python -I macro_gdp.py holdout --edge E [--window H] [--alpha A]
"""

import argparse
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import kx
import macro_cpi

XLSX = macro_cpi.RAW / "macro_sources" / "gdpnow_tracking.xlsx"


def gdpnow():
    d = pd.read_excel(XLSX, "TrackingArchives")
    d = d.rename(columns={"Forecast Date": "date", "Quarter being forecasted": "quarter"})
    d = d[["date", "quarter", "GDP Nowcast"]].rename(columns={"GDP Nowcast": "nowcast"})
    d["date"] = pd.to_datetime(d.date)
    d["quarter"] = pd.to_datetime(d.quarter).dt.to_period("Q")
    return d.dropna()


def calibrate():
    r = pd.read_excel(XLSX, "TrackRecord").iloc[:, [0, 1, 2]]
    r.columns = ["quarter", "nowcast", "actual"]
    r = r[pd.to_datetime(r.quarter, errors="coerce").notna()]
    q = pd.to_datetime(r.quarter).dt.to_period("Q")
    r = r[(q >= pd.Period("2014Q1")) & (q <= pd.Period("2021Q4"))]
    e = r.actual.astype(float) - r.nowcast.astype(float)
    return e.mean(), e.std(ddof=1), len(e)


def quarter_of(close):
    """The quarter whose advance estimate a market settles on: the last quarter end at least
    20 days before close (advance estimates come ~1 month after the quarter; Q4 2025 was late)."""
    return (close - pd.Timedelta(days=20)).tz_localize(None).to_period("Q") - 1


def markets():
    ts = []
    for root in macro_cpi.MACRO:
        if (root / "KXGDP" / "trades.parquet").exists():
            _, t = kx.load(["KXGDP"], root=root)
            ts.append(t)
    t = pd.concat(ts, ignore_index=True).drop_duplicates("trade_id")
    t["floor_strike"] = t.floor_strike.fillna(
        pd.to_numeric(t.ticker.str.extract(r"-T(-?[\d.]+)$")[0], errors="coerce")
    )
    t["strike_type"] = t.strike_type.fillna("greater")
    t["quarter"] = t.close_time.map(quarter_of)
    return t


def snapshot(t, gn, b, s, window=6):
    t = t.assign(tau=t.close_time - pd.Timedelta(hours=24))
    pre = t[t.ts < t.tau]
    rows = []
    for tk, g in pre.groupby("ticker"):
        g = g.sort_values("ts")
        r = g.iloc[-1]
        recent = g[g.ts >= r.tau - pd.Timedelta(hours=window)]
        ay = recent[recent.taker_side == "yes"].yes_price
        an = recent[recent.taker_side == "no"].yes_price
        day = r.tau.tz_convert("America/New_York").tz_localize(None).normalize()
        x = gn[(gn.quarter == r.quarter) & (gn.date < day)]
        rows.append(
            {
                "ticker": tk,
                "event": r.event_ticker,
                "close": r.close_time,
                "y": r.y,
                "last": r.yes_price,
                "ask_yes": ay.iloc[-1] if len(ay) else np.nan,
                "ask_no": 1 - an.iloc[-1] if len(an) else np.nan,
                "fee": r.fee_taker,
                "strike_type": r.strike_type,
                "floor_strike": r.floor_strike,
                "cap_strike": np.nan,
                "nowcast": x.nowcast.iloc[-1] if len(x) else np.nan,
            }
        )
    d = pd.DataFrame(rows).dropna(subset=["nowcast", "floor_strike"])
    d["q"] = macro_cpi.prob_yes(d, d.nowcast + b, s)
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["explore", "holdout"])
    ap.add_argument("--edge", type=float)
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--window", type=float, default=6, help="hours of asks before tau")
    a = ap.parse_args()
    b, s, n = calibrate()
    print(f"calibration 2014Q1..2021Q4: {n} quarters, advance - GDPNow mean {b:+.2f}, sd {s:.2f}")
    d = snapshot(markets(), gdpnow(), b, s, a.window)
    part = d[d.close < macro_cpi.SPLIT] if a.mode == "explore" else d[d.close >= macro_cpi.SPLIT]
    if a.mode == "holdout":
        f = macro_cpi.takers(part, a.edge)
        print(
            f"HOLDOUT GDPNow taker, edge {a.edge}, window {a.window} h: "
            f"{kx.fmt(kx.ratio_boot(f, 'event', alpha=a.alpha))}"
        )
        return
    print(f"exploration: {part.event.nunique()} events, {len(part)} markets")
    print(
        f"  Brier market (last trade) {((part['last'] - part.y) ** 2).mean():.4f}, "
        f"GDPNow {((part.q - part.y) ** 2).mean():.4f}, "
        f"blend {((0.5 * part.q + 0.5 * part['last'] - part.y) ** 2).mean():.4f}"
    )
    for edge in (0.0, 0.05, 0.10, 0.20):
        f = macro_cpi.takers(part, edge)
        print(f"  edge {edge:.2f}: {kx.fmt(kx.ratio_boot(f, 'event'))}")


if __name__ == "__main__":
    main()
