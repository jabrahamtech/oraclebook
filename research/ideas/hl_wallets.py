"""Round 7 (PROTOCOL_V7.md, Amendments 1-2): reverse the most profitable Hyperliquid accounts.
From the 200 accounts with the highest month PnL, select the top 10 eligible by exploration
closed PnL net of fees, fingerprint how they enter and hold, give each an archetype by fixed
cut-offs, and derive at most three rules (R-trend, R-fade, R-fund) from the wallets' medians,
with no tuning on PnL. The rules, not the wallets, are then tested: on Binance USDⓈ-M 1h klines
from 2024-01-01 to the window start (backtest), and on Hyperliquid 1h candles in the holdout.

Usage: python -I hl_wallets.py explore
       python -I hl_wallets.py backtest --rule trend --k K --hold H --coins BTC,ETH,SOL,...
              --alpha A                 (once per frozen rule, after the freeze is recorded)
       python -I hl_wallets.py holdout --rule trend --k K --hold H --coins ... --alpha A
"""

import argparse
import collections
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common as C

sys.path.insert(0, str(C.ROOT / "data"))
import hl_api as H

TD = pd.Timedelta
RUN = pd.Timestamp("2026-10-18", tz="UTC")
START = RUN - TD(days=90)
SPLIT = START + TD(days=54)
BACKTEST_FROM = pd.Timestamp("2024-01-01", tz="UTC")
WARMUP = TD(days=31)
POOL, TOP = 200, 10
MIN_VALUE, MIN_TRIPS, MIN_DAYS = 10_000, 100, 20
CUT, MM_SHARE, MM_HOLD, TREND_HOLD = 0.65, 0.7, TD("10min"), TD("1h")
COST = 2 * (0.00045 + 0.0001)
SIGMA_HOURS, SIGMA_MIN = 720, 360
HOURS_PER_YEAR = 8760
BASE = ("BTC", "ETH", "SOL")
EPS = 1e-9
OUT = C.ROOT / "data" / "out"


def root():
    return C.RAW / "hyperliquid"


def ms(t):
    return int(t.timestamp() * 1000)


def perp_fills(address):
    f = pd.read_parquet(root() / "fills" / f"{address}.parquet")
    f = f[~f.coin.str.startswith("@") & ~f.coin.str.contains("/", regex=False)]
    # The API's order chains `start_pos` within a block; sorting by tid would break the chain.
    return f.sort_values("time", kind="stable").reset_index(drop=True)


def trips(f):
    """Round trips per coin from `start_pos`: flat -> flat, or a flip (which closes one trip and
    opens the next). Trips still open at the end have close = NaN."""
    out = []
    for coin, g in f.groupby("coin", sort=False):
        start = None
        for t, p0, sz, side in zip(g.time, g.start_pos, g.sz, g.side, strict=True):
            p1 = round(p0 + (sz if side == "B" else -sz), 9)
            flat0, flat1 = abs(p0) < EPS, abs(p1) < EPS
            if start is not None and (flat0 or flat1 or p0 * p1 < 0):
                if not flat0:  # a fill from flat means the close was never seen: drop the trip
                    out.append((coin, start[0], t, start[1]))
                start = None
            if start is None and (flat0 or p0 * p1 < 0) and not flat1:
                start = (t, 1 if p1 > 0 else -1)
        if start is not None:
            out.append((coin, start[0], np.nan, start[1]))
    tr = pd.DataFrame(out, columns=["coin", "open", "close", "dir"])
    return tr.assign(hold=pd.to_timedelta(tr.close - tr.open, unit="ms"))


def ledger(f):
    """Amendment 3, per coin and first-in-first-out: entry orders (open or add; a flip opens the
    new side), reducing orders, and matched lots (open, close, size). A position held before the
    first fill seen, or left by missing fills, is a lot with no open time."""
    entries, closes, matched = [], [], []
    for coin, g in f.groupby("coin", sort=False):
        lots, held = collections.deque(), 0.0
        for t, oid, p0, sz, side in zip(g.time, g.oid, g.start_pos, g.sz, g.side, strict=True):
            d = sz if side == "B" else -sz
            if abs(abs(p0) - held) > 1e-6 * max(1.0, abs(p0)):
                lots, held = collections.deque([[None, abs(p0)]] if abs(p0) >= EPS else []), abs(p0)
            if abs(p0) < EPS or p0 * d > 0:
                lots.append([t, abs(d)])
                held += abs(d)
                entries.append((coin, oid, t, 1 if d > 0 else -1))
                continue
            closes.append((coin, oid, t))
            q = min(abs(d), abs(p0))
            held -= q
            while q > EPS and lots:
                take = min(q, lots[0][1])
                if lots[0][0] is not None:
                    matched.append((coin, lots[0][0], t, take))
                lots[0][1] -= take
                q -= take
                if lots[0][1] <= EPS:
                    lots.popleft()
            if abs(d) - abs(p0) > EPS:
                lots, held = collections.deque([[t, abs(d) - abs(p0)]]), abs(d) - abs(p0)
                entries.append((coin, oid, t, 1 if d > 0 else -1))
    return (pd.DataFrame(entries, columns=["coin", "oid", "open", "dir"])
            .drop_duplicates(["coin", "oid"]),
            pd.DataFrame(closes, columns=["coin", "oid", "time"]),
            pd.DataFrame(matched, columns=["coin", "open", "close", "size"]))


def weighted_median(x, w):
    if not len(x):
        return np.nan
    o = np.argsort(x)
    c = np.cumsum(np.asarray(w)[o])
    return np.asarray(x)[o][np.searchsorted(c, c[-1] / 2)]


def window(f, lo, hi, col="time"):
    return f[(f[col] >= ms(lo)) & (f[col] < ms(hi))]


def stats(address, lo, hi):
    """Closed PnL net of fees, closed trades (Amendment 3, and Amendment 2's round trips), active
    days, maker share, FIFO median hold, and the window's entry orders."""
    f = perp_fills(address)
    e = window(f, lo, hi)
    tr = window(trips(f), lo, hi, "open")
    entries, closes, lots = ledger(f)
    lots = window(window(lots, lo, hi, "open"), lo, hi, "close")
    hold = weighted_median((lots.close - lots.open).to_numpy(), lots["size"].to_numpy())
    return {
        "address": address, "pnl": e.closed_pnl.sum() - e.fee.sum(),
        "trades": window(closes, lo, hi).oid.nunique(), "trips": int((tr.close < ms(hi)).sum()),
        "days": pd.to_datetime(e.time, unit="ms").dt.floor("D").nunique(),
        "maker": 1 - e.crossed.mean() if len(e) else np.nan,
        "hold": pd.to_timedelta(hold, unit="ms"), "fills": len(e),
        "notional": (e.px * e.sz).groupby(e.coin).sum(),
    }, window(entries, lo, hi, "open")


def select():
    lb = pd.read_parquet(root() / "leaderboard.parquet").nlargest(POOL, "month_pnl")
    w = lb.merge(pd.read_parquet(root() / "wallets.parquet"), on="address", how="left")
    funnel = [("top 200 by month PnL", len(w))]
    w = w[w.account_value >= MIN_VALUE]
    funnel.append(("value >= $10k", len(w)))
    w = w[w.role != "vault"]
    funnel.append(("not a vault", len(w)))
    w = w[~(w.truncated.astype(bool) | w.late.astype(bool))]
    funnel.append(("exploration fills served", len(w)))
    rows, entries = [], {}
    for a in w.address:
        s, opened = stats(a, START, SPLIT)
        rows.append(s)
        entries[a] = opened
    s = pd.DataFrame(rows).merge(w[["address", "account_value", "month_pnl", "leverage"]])
    a2 = s[(s.trips >= MIN_TRIPS) & (s.days >= MIN_DAYS)]
    print("eligibility: " + " -> ".join(f"{k} {n}" for k, n in funnel)
          + f"; Amendment 2 (round trips >= 100, >= 20 days): {len(a2)}")
    s = s[s.trades >= MIN_TRIPS]
    funnel.append((">= 100 closing orders", len(s)))
    s = s[s.days >= MIN_DAYS]
    funnel.append((">= 20 active days", len(s)))
    print("eligibility, Amendment 3: " + " -> ".join(f"{k} {n}" for k, n in funnel[-2:]))
    top = s.nlargest(TOP, "pnl").reset_index(drop=True)
    return top, {a: entries[a] for a in top.address}


def hourly(k):
    """Hourly closes on a regular grid, with sigma, r4/r24 and z4/z24 known at each close."""
    k = k.assign(ts=pd.to_datetime(k.t, unit="ms", utc=True)).set_index("ts").sort_index()
    k = k[~k.index.duplicated()]
    k = k.reindex(pd.date_range(k.index.min(), k.index.max(), freq="h"))
    lc = np.log(k.close)
    sigma = lc.diff().rolling(SIGMA_HOURS, min_periods=SIGMA_MIN).std()
    d = pd.DataFrame({"close": k.close, "sigma": sigma, "r4": lc - lc.shift(4),
                      "r24": lc - lc.shift(24)})
    d["z4"], d["z24"] = d.r4 / (2 * d.sigma), d.r24 / (np.sqrt(24) * d.sigma)
    d.index = (d.index + TD("1h")).astype(C.UTC)  # known when the candle closes
    return d.rename_axis("known").reset_index()


def funding(coin):
    p = H.coin_file(root() / "funding", coin)
    if not p.exists():
        return pd.DataFrame({"hour": pd.Series(dtype=C.UTC), "rate": pd.Series(dtype=float)})
    f = pd.read_parquet(p)
    hour = pd.to_datetime(f.time, unit="ms", utc=True).dt.floor("h").astype(C.UTC)
    return pd.DataFrame({"hour": hour, "rate": f.rate}).drop_duplicates("hour").sort_values("hour")


def market(coins, source, lo, hi):
    """Hourly signals and funding per coin for decisions in [lo, hi), from Hyperliquid or Binance
    candles (fetched once and cached, from lo minus the sigma warm-up)."""
    folder = root() / ("candles" if source == "hl" else "binance_um_1h")
    need = [c for c in coins if not H.coin_file(folder, c).exists()]
    day = "%Y-%m-%d"
    if need:
        fetch = H.candles if source == "hl" else H.binance
        fetch(root(), need, (lo - WARMUP).strftime(day), hi.strftime(day))
    H.funding(root(), coins, lo.strftime(day), RUN.strftime(day))
    out = {}
    for c in coins:
        p = H.coin_file(folder, c)
        if p.exists() and len(k := pd.read_parquet(p)):
            out[c] = (hourly(k), funding(c))
    return out


def fingerprint(opened, mkt):
    """Entry behaviour against the prior 4h / 24h move and funding's sign (exploration)."""
    e = opened.assign(ts=pd.to_datetime(opened.open, unit="ms", utc=True).astype(C.UTC))
    parts = []
    for coin, g in e.groupby("coin"):
        if coin not in mkt:
            continue
        sig, fund = mkt[coin]
        x = pd.merge_asof(g.sort_values("ts"), sig, left_on="ts", right_on="known")
        x = pd.merge_asof(x, fund, left_on="ts", right_on="hour")
        parts.append(x)
    x = pd.concat(parts, ignore_index=True).dropna(subset=["z24", "z4"]) if parts else None
    if x is None or x.empty:
        return dict.fromkeys(["follow24", "fade4", "fund_opp", "z24", "z4", "apr", "hour",
                              "hour_share"], np.nan) | {"covered": 0.0}
    hours = x.ts.dt.hour.value_counts(normalize=True)
    fx = x.dropna(subset=["rate"])
    fx = fx[fx.rate != 0]
    return {
        "covered": len(x) / len(e),
        "follow24": (x.dir * x.r24 > 0).mean(), "fade4": (x.dir * x.r4 < 0).mean(),
        "fund_opp": (fx.dir * fx.rate < 0).mean() if len(fx) else np.nan,
        "z24": x.z24.abs().median(), "z4": x.z4.abs().median(),
        "apr": (fx.rate.abs() * HOURS_PER_YEAR).median() if len(fx) else np.nan,
        "hour": hours.index[0], "hour_share": hours.iloc[0],
    }


def archetype(r):
    if r.maker >= MM_SHARE and r.hold < MM_HOLD:
        return "market maker"
    if r.follow24 >= CUT and r.hold >= TREND_HOLD:
        return "trend"
    if r.fade4 >= CUT:
        return "fade"
    if r.fund_opp >= CUT:
        return "funding-contrarian"
    return "other"


def money(x):
    return f"{'+' if x >= 0 else '-'}${abs(x) / 1e6:.2f}M" if abs(x) >= 1e5 else f"{x:+,.0f}"


def explore():
    top, entries = select()
    coins = sorted({c for a in top.address for c in entries[a].coin.unique()})
    mkt = market(coins, "hl", START, RUN)
    rows = []
    for r in top.itertuples():
        fp = fingerprint(entries[r.address], mkt)
        rows.append({**r._asdict(), **fp})
    t = pd.DataFrame(rows)
    t["archetype"] = t.apply(archetype, axis=1)
    t.drop(columns=["notional", "Index"]).to_csv(OUT / "hl_wallets.csv", index=False)
    for r in t.itertuples():
        lead = "/".join(r.notional.nlargest(3).index)
        lev = f"{r.leverage:g}x" if pd.notna(r.leverage) else "n/a"
        hour = f"{int(r.hour):02d}h({r.hour_share:.0%})" if pd.notna(r.hour) else "n/a"
        print(f"{r.address[:8]}…{r.address[-4:]} pnl={money(r.pnl)} trades={r.trades:,} "
              f"days={r.days} maker={r.maker:.2f} hold={r.hold / TD('1h'):.1f}h lev={lev} "
              f"coins={lead} follow24={r.follow24:.2f} fade4={r.fade4:.2f} "
              f"fund_opp={r.fund_opp:.2f} hour={hour} cov={r.covered:.0%} -> {r.archetype}")
    notional = pd.concat(list(t.notional)).groupby(level=0).sum().sort_values(ascending=False)
    extra = [c for c in notional.index if c not in BASE][:3]
    universe = ",".join([*BASE, *extra])
    print(f"universe: {universe}")
    for arch, rule, col in (("trend", "trend", "z24"), ("fade", "fade", "z4"),
                            ("funding-contrarian", "fund", "apr")):
        g = t[t.archetype == arch]
        if g.empty:
            print(f"R-{rule}: no {arch} wallet, not frozen")
            continue
        k = g[col].median()
        hold = max(1, round(g.hold.median() / TD("1h")))
        unit = "APR" if rule == "fund" else "sigma"
        print(f"R-{rule}: k={k:.3f} {unit}, H={hold}h, from {len(g)} wallets -> "
              f"backtest --rule {rule} --k {k:.3f} --hold {hold} --coins {universe}")


def simulate(sig, fund, rule, k, hold, lo, hi):
    """Trades of one rule on one coin: decisions at hourly closes in [lo, hi - hold], one position
    at a time, entry and exit at the closes, costs and funding settled during the hold."""
    d = sig.set_index("known")
    f = fund.set_index("hour").rate
    cum = f.cumsum()
    apr = f.reindex(d.index, method="ffill") * HOURS_PER_YEAR
    if rule == "trend":
        side = np.where(d.z24.abs() >= k, np.sign(d.r24), 0)
    elif rule == "fade":
        side = np.where(d.z4.abs() >= k, -np.sign(d.r4), 0)
    else:
        side = np.where(apr >= k, -1, np.where(apr <= -k, 1, 0))
    times, close = d.index, d.close.to_numpy()
    step = TD(hours=hold)
    first_fund = f.index.min() if len(f) else pd.Timestamp.max.tz_localize("UTC")
    rows, free = [], lo
    for i in np.flatnonzero(side != 0):
        t = times[i]
        if t < free or t < lo or t + step > hi or t < first_fund or i + hold >= len(times):
            continue
        p0, p1 = close[i], close[i + hold]
        if not (np.isfinite(p0) and np.isfinite(p1)):
            continue
        paid = cum.asof(t + step) - cum.asof(t)
        s = side[i]
        rows.append({"ts": t, "dir": s, "ret": s * (p1 / p0 - 1) - COST - s * paid})
        free = t + step
    return pd.DataFrame(rows, columns=["ts", "dir", "ret"])


def test(mode, rule, k, hold, coins, alpha):
    lo, hi, source = (BACKTEST_FROM, START, "binance") if mode == "backtest" else (SPLIT, RUN, "hl")
    mkt = market(coins, source, lo, hi)
    parts = [simulate(*mkt[c], rule, k, hold, lo, hi).assign(coin=c) for c in coins if c in mkt]
    tr = pd.concat(parts, ignore_index=True)
    tr["cluster"] = tr.ts.dt.strftime("%G-W%V")
    s = C.summarise(tr, alpha=alpha)
    print(f"{mode.upper()} R-{rule} k={k} H={hold}h coins={','.join(mkt)} alpha={alpha}: "
          f"{C.fmt(s)}")
    if mode == "backtest":
        ok = s.get("n", 0) >= 200 and s["clusters"] >= 20 and s["lo"] > 0
        print("  verdict (backtest half): "
              + ("PASS; the holdout must have the same sign" if ok else "FAIL; the rule fails"))
    return s


def persistence():
    """Skill persistence (reported, not tested): the selected wallets' holdout PnL."""
    top = pd.read_csv(OUT / "hl_wallets.csv")
    pnl = [stats(a, SPLIT, RUN)[0]["pnl"] for a in top.address]
    pos = sum(p > 0 for p in pnl)
    print(f"selected wallets in the holdout: {pos}/{len(pnl)} positive, total {money(sum(pnl))}")


def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("mode", choices=["explore", "backtest", "holdout"])
    ap.add_argument("--rule", choices=["trend", "fade", "fund"])
    ap.add_argument("--k", type=float)
    ap.add_argument("--hold", type=int)
    ap.add_argument("--coins", default=",".join(BASE))
    ap.add_argument("--alpha", type=float, default=0.05)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"window {START:%Y-%m-%d} → {RUN:%Y-%m-%d}, exploration to {SPLIT:%Y-%m-%d}")
    if a.mode == "explore":
        explore()
        return
    test(a.mode, a.rule, a.k, a.hold, a.coins.split(","), a.alpha)
    if a.mode == "holdout":
        persistence()


if __name__ == "__main__":
    main()
