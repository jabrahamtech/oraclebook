"""Cleveland Fed CPI nowcast vs Kalshi CPI month-over-month brackets. See PROTOCOL_MACRO.md.

Usage: python -I macro_cpi.py calibrate
       python -I macro_cpi.py explore
       python -I macro_cpi.py holdout --edge E [--alpha A]
"""

import argparse
import json
import math
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import kx

RAW = pathlib.Path(__file__).resolve().parents[1] / "data" / "raw"
MACRO = (RAW / "macro_hist" / "kalshi_api", RAW / "macro" / "kalshi_api")
SPLIT = pd.Timestamp("2025-01-01", tz="UTC")
CAL = ("2014-01", "2021-12")
PHI = np.vectorize(lambda z: 0.5 * (1 + math.erf(z / math.sqrt(2))))
MONTHS = {
    m: i
    for i, m in enumerate(
        ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1
    )
}


def nowcasts():
    """Long table: target month, release date, nowcast date, headline CPI MoM nowcast; and the
    actual print per target month."""
    charts = json.loads((RAW / "macro_sources" / "cleveland_nowcast_month.json").read_text())
    rows, actual = [], {}
    for c in charts:
        y, m = map(int, c["chart"]["subcaption"].split("-"))
        target = pd.Period(year=y, month=m, freq="M")
        cats = c["categories"][0]["category"]
        ds = {s["seriesname"]: s["data"] for s in c["dataset"]}
        now, act = ds.get("CPI Inflation"), ds.get("Actual CPI Inflation")
        if now is None:
            continue
        release, dates = None, []
        prev = None
        for cat in cats:
            lab = cat.get("label", "")
            if cat.get("vline"):
                if lab == f"CPI {target.strftime('%b')}" and prev is not None:
                    release = prev
                continue
            mm, dd = map(int, lab.split("/"))
            yy = y + (1 if mm < m else 0)
            prev = pd.Timestamp(year=yy, month=mm, day=dd)
            dates.append(prev)
        vals = [float(v["value"]) if v.get("value") not in ("", None) else np.nan for v in now]
        n = min(len(dates), len(vals))
        rows.append(
            pd.DataFrame(
                {"target": target, "release": release, "date": dates[:n], "nowcast": vals[:n]}
            )
        )
        if act:
            a = [float(v["value"]) for v in act if v.get("value") not in ("", None)]
            if a:
                actual[target] = a[0]
    return pd.concat(rows, ignore_index=True).dropna(subset=["nowcast"]), pd.Series(actual)


def as_of(nc, target, before):
    """Latest nowcast for `target` dated strictly before the calendar date `before` (US Eastern
    date when `before` is a UTC timestamp; the nowcast's dates are Eastern)."""
    if before.tzinfo is not None:
        before = before.tz_convert("America/New_York").tz_localize(None)
    x = nc[(nc.target == target) & (nc.date < before.normalize())]
    return x.nowcast.iloc[-1] if len(x) else np.nan


def calibrate():
    nc, actual = nowcasts()
    errs = []
    for target, g in nc.groupby("target"):
        if not (pd.Period(CAL[0]) <= target <= pd.Period(CAL[1])) or target not in actual:
            continue
        rel = g.release.dropna()
        if rel.empty:
            continue
        tau = rel.iloc[0] - pd.Timedelta(days=1)
        v = as_of(nc, target, tau)
        if not np.isnan(v):
            errs.append(actual[target] - v)
    e = np.array(errs)
    return e.mean(), e.std(ddof=1), len(e)


def markets():
    ms = []
    for root in MACRO:
        if (root / "KXCPI" / "markets.parquet").exists():
            _, t = kx.load(["KXCPI"], root=root)
            ms.append(t)
    t = pd.concat(ms, ignore_index=True).drop_duplicates("trade_id")
    # Older events (CPI-21DEC-T0.7) carry the strike only in the ticker.
    t["floor_strike"] = t.floor_strike.fillna(
        pd.to_numeric(t.ticker.str.extract(r"-T(-?[\d.]+)$")[0], errors="coerce")
    )
    t["target"] = t.event_ticker.map(target_of)
    return t


def target_of(event):
    code = event.split("-")[-1]
    return pd.Period(year=2000 + int(code[:2]), month=MONTHS[code[2:5]], freq="M")


def prob_yes(t, mu, sd):
    def above(x):  # P(unrounded >= x)
        return 1 - PHI((x - mu) / sd)

    st, f, c = t.strike_type.to_numpy(), t.floor_strike.to_numpy(), t.cap_strike.to_numpy()
    return np.where(
        st == "greater",
        above(f + 0.05),
        np.where(st == "less", 1 - above(c - 0.05), above(f - 0.05) - above(c + 0.05)),
    )


def snapshot(t, nc, b, s):
    """Per market at tau = close - 24 h: last trade, last asks for YES / NO in the prior 6 h,
    the model probability, and the outcome."""
    t = t.assign(tau=t.close_time - pd.Timedelta(hours=24))
    pre = t[t.ts < t.tau]
    rows = []
    for tk, g in pre.groupby("ticker"):
        g = g.sort_values("ts")
        r = g.iloc[-1]
        recent = g[g.ts >= r.tau - pd.Timedelta(hours=6)]
        ay = recent[recent.taker_side == "yes"].yes_price
        an = recent[recent.taker_side == "no"].yes_price
        v = as_of(nc, r.target, r.tau)
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
                "cap_strike": r.get("cap_strike", np.nan),
                "nowcast": v,
            }
        )
    d = pd.DataFrame(rows).dropna(subset=["nowcast"])
    d["q"] = prob_yes(d, d.nowcast + b, s)
    return d


def takers(d, edge):
    out = []
    for side in ("yes", "no"):
        p = d[f"ask_{side}"]
        q = d.q if side == "yes" else 1 - d.q
        fee = d.fee * p * (1 - p)
        sel = (q - p - fee > edge) & p.notna()
        x = d[sel]
        y = x.y if side == "yes" else 1 - x.y
        pp, ff = p[sel], fee[sel]
        out.append(x.assign(side=side, count=1.0, cost=pp + ff, pnl=y - pp - ff))
    return pd.concat(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["calibrate", "explore", "holdout"])
    ap.add_argument("--edge", type=float)
    ap.add_argument("--alpha", type=float, default=0.05)
    a = ap.parse_args()
    b, s, n = calibrate()
    print(f"calibration {CAL[0]}..{CAL[1]}: {n} months, actual - nowcast mean {b:+.3f}, sd {s:.3f}")
    if a.mode == "calibrate":
        return
    nc, _ = nowcasts()
    d = snapshot(markets(), nc, b, s)
    part = d[d.close < SPLIT] if a.mode == "explore" else d[d.close >= SPLIT]
    if a.mode == "holdout":
        f = takers(part, a.edge)
        print(
            f"HOLDOUT CPI nowcast taker, edge {a.edge}: "
            f"{kx.fmt(kx.ratio_boot(f, 'event', alpha=a.alpha))}"
        )
        return
    print(f"exploration: {part.event.nunique()} events, {len(part)} markets")
    print(
        f"  Brier market (last trade) {((part['last'] - part.y) ** 2).mean():.4f}, "
        f"nowcast {((part.q - part.y) ** 2).mean():.4f}, "
        f"blend {((0.5 * part.q + 0.5 * part['last'] - part.y) ** 2).mean():.4f}"
    )
    for edge in (0.0, 0.05, 0.10, 0.20):
        f = takers(part, edge)
        print(f"  edge {edge:.2f}: {kx.fmt(kx.ratio_boot(f, 'event'))}")


if __name__ == "__main__":
    main()
