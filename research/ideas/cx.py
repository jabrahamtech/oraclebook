"""Round 4 data: Kalshi daily BTC / ETH "above K" ladders (the 17:00 ET event, the only hour
listed since Nov 2024) with Binance 1-minute spot, for the ML / foundation-model / bull-regime
tests (PROTOCOL_V4.md).

fetch: for each day, the event KX{BTC,ETH}D-<yymondd>17 (from /historical/* before Kalshi's
cutoff, the live API after), the strikes within ±BAND (2%) of the Binance price 60 minutes before
the close (outcome-blind), and their trades in the last 65 minutes.
build: one row per (market, decision time t in {60, 30, 15, 5} min before close): market state
(last trade, last taker-buy ask of YES / NO and its age, flow in the previous 15 min), Binance
features (distance to strike in sigmas, returns, realised volatility, 7- and 30-day trend),
outcome.

Usage: python -I cx.py fetch [--start 2024-11-01] [--end 2026-10-14]
       python -I cx.py build
"""

import argparse
import glob
import pathlib
import sys
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "data"))
import kalshi_api

RAW = pathlib.Path(__file__).resolve().parents[1] / "data" / "raw"
OUT = RAW / "cx"
CUTOFF = pd.Timestamp("2026-08-15", tz="UTC")
BAND = 0.02
ASSETS = ("BTC", "ETH")
DECISIONS = (60, 30, 15, 5)
COLS = ["trade_id", "created_time", "yes_price", "count", "taker_side"]


def spot(asset):
    """Binance 1-minute closes (UTC minute index)."""
    files = sorted(glob.glob(str(RAW / "binance_klines_1m" / f"spot__{asset}USDT-1m-*.csv")))
    k = pd.concat(pd.read_csv(f, header=None, usecols=[0, 4]) for f in files)
    k.columns = ["t", "close"]
    # Binance switched open times from ms to µs in 2025.
    ms = np.where(k.t > 1e14, k.t // 1000, k.t)
    s = pd.Series(k.close.to_numpy(), index=pd.to_datetime(ms, unit="ms", utc=True))
    s = s.sort_index()
    return s[~s.index.duplicated()]


def event_close(day):
    """17:00 America/New_York on `day`, in UTC."""
    return pd.Timestamp(day.date(), tz="America/New_York").replace(hour=17).tz_convert("UTC")


def fetch_event(asset, day, px, lim):
    code = day.strftime("%y%b%d").upper() + "17"
    ev = f"KX{asset}D-{code}"
    close = event_close(day)
    hist = close < CUTOFF
    path = "/historical/markets" if hist else "/markets"
    d = kalshi_api.get(f"{path}?event_ticker={ev}&limit=200", lim)
    ms = [m for m in d.get("markets", []) if m.get("strike_type") == "greater"]
    if not ms:
        return None, None
    ref = px.asof(close - pd.Timedelta(minutes=60))
    keep = [m for m in ms if abs(float(m["floor_strike"]) / ref - 1) <= BAND]
    hi = int((close - kalshi_api.EPOCH) // pd.Timedelta(seconds=1))
    parts = []
    for m in keep:
        _, rows = kalshi_api.trades(m["ticker"], lim, hi - 65 * 60, hi + 60, hist)
        parts.append(pd.DataFrame(rows, columns=COLS).assign(ticker=m["ticker"]))
    meta = pd.DataFrame(
        [
            {
                "ticker": m["ticker"],
                "event": ev,
                "asset": asset,
                "close_time": close,
                "strike": float(m["floor_strike"]),
                "result": m.get("result"),
                "volume": float(m.get("volume_fp") or 0),
            }
            for m in keep
        ]
    )
    return meta, (pd.concat(parts, ignore_index=True) if parts else None)


def fetch(start, end):
    OUT.mkdir(parents=True, exist_ok=True)
    lim = kalshi_api.Limiter(5)
    for asset in ASSETS:
        px = spot(asset)
        days = pd.date_range(start, end, freq="D")
        mp, tp = OUT / f"{asset}_markets.parquet", OUT / f"{asset}_trades.parquet"
        have = set(pd.read_parquet(mp).event) if mp.exists() else set()
        metas = [pd.read_parquet(mp)] if mp.exists() else []
        trades = [pd.read_parquet(tp)] if tp.exists() else []
        todo = [d for d in days if f"KX{asset}D-{d.strftime('%y%b%d').upper()}17" not in have]
        with ThreadPoolExecutor(max_workers=4) as ex:
            for i, (meta, tr) in enumerate(ex.map(lambda d: fetch_event(asset, d, px, lim), todo)):
                if meta is not None:
                    metas.append(meta)
                if tr is not None:
                    trades.append(tr)
                if i % 50 == 49 or i == len(todo) - 1:
                    pd.concat(metas, ignore_index=True).to_parquet(mp, index=False)
                    pd.concat(trades, ignore_index=True).to_parquet(tp, index=False)
                    print(f"{asset}: {i + 1}/{len(todo)} days", flush=True)


def features(px, t):
    """Binance features known at time t (UTC minute). Bars are indexed by open time, so the bar
    opened at t - 1 min is the last one closed by t."""
    lr = np.log(px)
    s = lr.loc[: t - pd.Timedelta(minutes=1)].iloc[-60 * 24 * 31 :]
    if len(s) < 60 * 24 * 8:
        return None
    r1 = s.diff()
    return {
        "spot": float(np.exp(s.iloc[-1])),
        "ret_5m": s.iloc[-1] - s.iloc[-6],
        "ret_15m": s.iloc[-1] - s.iloc[-16],
        "ret_60m": s.iloc[-1] - s.iloc[-61],
        "ret_1d": s.iloc[-1] - s.iloc[-1441],
        "ret_7d": s.iloc[-1] - s.iloc[-1440 * 7 - 1],
        "ret_30d": s.iloc[-1] - s.iloc[0],
        "vol_60m": r1.iloc[-60:].std(),
        "vol_1d": r1.iloc[-1440:].std(),
    }


def build():
    rows = []
    for asset in ASSETS:
        if not (OUT / f"{asset}_markets.parquet").exists():
            continue
        px = spot(asset)
        m = pd.read_parquet(OUT / f"{asset}_markets.parquet")
        m = m[m.result.isin(["yes", "no"])]
        tr = pd.read_parquet(OUT / f"{asset}_trades.parquet")
        tr["ts"] = pd.to_datetime(tr.created_time, utc=True, format="ISO8601")
        tr = tr.sort_values("ts")
        by = dict(tuple(tr.groupby("ticker")))
        for ev, g in m.groupby("event"):
            close = g.close_time.iloc[0]
            ref = px.asof(close - pd.Timedelta(minutes=60))
            g = g[(g.strike / ref - 1).abs() <= BAND]
            for left in DECISIONS:
                t = (close - pd.Timedelta(minutes=left)).floor("min")
                f = features(px, t)
                if f is None:
                    continue
                for _, r in g.iterrows():
                    x = by.get(r.ticker)
                    pre = x[x.ts < t] if x is not None else None
                    last = pre.iloc[-1].yes_price if pre is not None and len(pre) else np.nan
                    w = pre[pre.ts >= t - pd.Timedelta(minutes=15)] if pre is not None else None
                    wy = w[w.taker_side == "yes"] if w is not None else None
                    wn = w[w.taker_side == "no"] if w is not None else None
                    ay = wy.yes_price if wy is not None else pd.Series()
                    an = wn.yes_price if wn is not None else pd.Series()
                    age_y = (t - wy.ts.iloc[-1]).total_seconds() if len(ay) else np.nan
                    age_n = (t - wn.ts.iloc[-1]).total_seconds() if len(an) else np.nan
                    buy = w[w.taker_side == "yes"]["count"].sum() if w is not None else 0
                    sell = w[w.taker_side == "no"]["count"].sum() if w is not None else 0
                    sig = f["vol_60m"] * np.sqrt(left) or np.nan
                    rows.append(
                        {
                            "asset": asset,
                            "event": ev,
                            "ticker": r.ticker,
                            "close": close,
                            "left": left,
                            "strike": r.strike,
                            "y": float(r.result == "yes"),
                            "last": last,
                            "ask_yes": ay.iloc[-1] if len(ay) else np.nan,
                            "ask_no": 1 - an.iloc[-1] if len(an) else np.nan,
                            "age_yes": age_y,
                            "age_no": age_n,
                            "flow_imb": (buy - sell) / (buy + sell) if buy + sell else 0.0,
                            "flow_n": buy + sell,
                            "z": np.log(f["spot"] / r.strike) / sig,
                            **f,
                        }
                    )
    d = pd.DataFrame(rows)
    d.to_parquet(OUT / "dataset.parquet", index=False)
    print(
        f"dataset: {len(d):,} rows, {d.event.nunique()} events, "
        f"{d.close.min():%Y-%m-%d} -> {d.close.max():%Y-%m-%d}"
    )


def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("mode", choices=["fetch", "build"])
    ap.add_argument("--start", default="2024-11-01")
    ap.add_argument("--end", default="2026-10-14")
    a = ap.parse_args()
    fetch(a.start, a.end) if a.mode == "fetch" else build()


if __name__ == "__main__":
    main()
