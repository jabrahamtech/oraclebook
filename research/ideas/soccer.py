"""Round 5 (PROTOCOL_V5.md): Kalshi soccer 1X2 markets against sharp closing odds.

Usage: python -I soccer.py build        # join Kalshi trades to football-data odds (fetches fees)
       python -I soccer.py explore
       python -I soccer.py holdout --tau T --edge E --bench bfe|avg [--alpha A]
"""

import argparse
import difflib
import json
import pathlib
import re
import subprocess
import sys
import unicodedata

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import kx

RAW = pathlib.Path(__file__).resolve().parents[1] / "data" / "raw"
FD = RAW / "football_data"
KALSHI = [RAW / "soccer_hist" / "kalshi_api", RAW / "soccer_live" / "kalshi_api"]
OUT = RAW / "soccer_v5"
SPLIT = pd.Timestamp("2026-08-15", tz="UTC")
STALE = pd.Timedelta(minutes=15)
LEAGUES = {
    "KXEPLGAME": "E0",
    "KXEFLCHAMPIONSHIPGAME": "E1",
    "KXEFLL1GAME": "E2",
    "KXSCOTTISHPREMGAME": "SC0",
    "KXBUNDESLIGAGAME": "D1",
    "KXBUNDESLIGA2GAME": "D2",
    "KXSERIEAGAME": "I1",
    "KXSERIEBGAME": "I2",
    "KXLALIGAGAME": "SP1",
    "KXLALIGA2GAME": "SP2",
    "KXLIGUE1GAME": "F1",
    "KXLIGUE2GAME": "F2",
    "KXEREDIVISIEGAME": "N1",
    "KXBELGIANPLGAME": "B1",
    "KXLIGAPORTUGALGAME": "P1",
    "KXSUPERLIGGAME": "T1",
    "KXSLGREECEGAME": "G1",
}
DROP = {
    "fc",
    "cf",
    "afc",
    "ac",
    "sc",
    "ssc",
    "as",
    "us",
    "sv",
    "vfb",
    "vfl",
    "rc",
    "ud",
    "cd",
    "sd",
}
ALIAS = {
    "wolves": "wolverhampton",
    "man united": "manchester united",
    "man city": "manchester city",
    "atletico": "ath madrid",
    "athletic club": "ath bilbao",
    "athletic bilbao": "ath bilbao",
    "stade rennais": "rennes",
    "paris": "paris sg",
    "psg": "paris sg",
    "vicente barcelos": "gil vicente",
    "sporting cp": "sp lisbon",
    "hellas verona": "verona",
}


def norm(name):
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    return " ".join(w for w in re.findall(r"[a-z0-9]+", s) if w not in DROP)


def sim(a, b):
    """Name similarity in [0, 1]. Football-data abbreviates (Nott'm Forest, Man United, Wolves);
    a shared 4-letter prefix of the first words counts as a strong match."""
    a, b = norm(a), norm(b)
    a, b = ALIAS.get(a, a), ALIAS.get(b, b)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 1.0
    a0, b0 = a.split()[0], b.split()[0]
    if len(a0) >= 4 and len(b0) >= 4 and a0[:4] == b0[:4]:
        return 0.9
    return difflib.SequenceMatcher(None, a, b).ratio()


def odds():
    rows = []
    for f in sorted(FD.glob("*.csv")):
        d = (
            pd.read_csv(f, encoding="latin-1", on_bad_lines="skip")
            .dropna(subset=["HomeTeam"])
            .copy()
        )
        local = pd.to_datetime(d.Date + " " + d.Time, format="%d/%m/%Y %H:%M", errors="coerce")
        d["kickoff"] = local.dt.tz_localize("Europe/London", ambiguous="NaT").dt.tz_convert("UTC")
        for bench, cols in (
            ("bfe", ("BFECH", "BFECD", "BFECA")),
            ("avg", ("AvgCH", "AvgCD", "AvgCA")),
        ):
            inv = 1 / d[list(cols)].apply(pd.to_numeric, errors="coerce")
            fair = inv.div(inv.sum(axis=1), axis=0)
            for o, c in zip("HDA", cols, strict=True):
                d[f"{bench}_{o}"] = fair[c]
        rows.append(d.assign(league=f.stem.split("_")[1]))
    keep = ["league", "kickoff", "HomeTeam", "AwayTeam", "FTR"]
    keep += [f"{b}_{o}" for b in ("bfe", "avg") for o in "HDA"]
    return pd.concat(rows, ignore_index=True)[keep].dropna(subset=["kickoff"])


def fees():
    path = OUT / "fees.json"
    if path.exists():
        return json.loads(path.read_text())
    out = {}
    for s in LEAGUES:
        r = subprocess.run(
            ["curl", "-s", "-m", "30", f"https://api.elections.kalshi.com/trade-api/v2/series/{s}"],
            capture_output=True,
            text=True,
            check=False,
        )
        x = json.loads(r.stdout or "{}").get("series", {})
        out[s] = float(x.get("fee_multiplier") or 1) * 0.07
    path.write_text(json.dumps(out, indent=1))
    return out


def occurrence(m):
    """Kick-off + 3 h: occurrence_datetime, or expected_expiration_time on older markets."""
    occ = pd.to_datetime(m.occurrence_datetime, utc=True, format="ISO8601", errors="coerce")
    exp = pd.to_datetime(m.expected_expiration_time, utc=True, format="ISO8601", errors="coerce")
    return occ.fillna(exp)


def kalshi():
    ms, ts = [], []
    for root in KALSHI:
        for s in LEAGUES:
            d = root / s
            if (d / "trades.parquet").exists():
                ms.append(pd.read_parquet(d / "markets.parquet"))
                ts.append(pd.read_parquet(d / "trades.parquet"))
    m = pd.concat(ms, ignore_index=True).drop_duplicates("ticker")
    t = pd.concat(ts, ignore_index=True).drop_duplicates("trade_id")
    m["occ"] = occurrence(m)
    t["ts"] = pd.to_datetime(t.created_time, utc=True, format="ISO8601")
    return m, t


def join(m, fd):
    """Kalshi event → football-data match: same league, kick-off within 15 min of occ − 3 h,
    best team-name match. Each team market is mapped to H or A, the tie market to D."""
    out = []
    for ev, g in m.groupby("event_ticker"):
        teams = g[g.yes_sub_title.str.lower() != "tie"]
        tie = g[g.yes_sub_title.str.lower() == "tie"]
        if len(teams) != 2 or len(tie) != 1:
            continue
        ref = g.occ.iloc[0] - pd.Timedelta(hours=3)
        c = fd[(fd.league == LEAGUES[g.series.iloc[0]]) & ((fd.kickoff - ref).abs() <= STALE)]
        best = None
        for _, r in c.iterrows():
            t1, t2 = teams.yes_sub_title.tolist()
            for h, a in ((t1, t2), (t2, t1)):
                s = min(sim(h, r.HomeTeam), sim(a, r.AwayTeam))
                if best is None or s > best[0]:
                    best = (s, r, h)
        if best is None or best[0] < 0.6:
            continue
        s, r, home = best
        for _, mk in g.iterrows():
            o = (
                "D"
                if mk.yes_sub_title.lower() == "tie"
                else ("H" if mk.yes_sub_title == home else "A")
            )
            out.append(
                {
                    "event": ev,
                    "ticker": mk.ticker,
                    "series": mk.series,
                    "outcome": o,
                    "kickoff": r.kickoff,
                    "y": float(mk.result == "yes"),
                    "ftr_check": float(r.FTR == o),
                    "match_score": s,
                    **{f"{b}": r[f"{b}_{o}"] for b in ("bfe", "avg")},
                }
            )
    return pd.DataFrame(out)


def quotes(rows, t, tau):
    """Last YES-taker and NO-taker prints in the 15 minutes up to kick-off − tau."""
    t = t[t.ticker.isin(rows.ticker)].merge(rows[["ticker", "kickoff"]], on="ticker")
    at = t.kickoff - pd.Timedelta(minutes=tau)
    w = t[(t.ts <= at) & (t.ts > at - STALE)].sort_values("ts")
    last = w.groupby(["ticker", "taker_side"]).yes_price.last().unstack()
    q = pd.DataFrame(index=rows.ticker.unique())
    q["ask_yes"] = last.get("yes")
    q["ask_no"] = 1 - last.get("no") if "no" in last else np.nan
    q["last"] = w.groupby("ticker").yes_price.last()
    return rows.join(q, on="ticker").assign(tau=tau)


def build():
    OUT.mkdir(parents=True, exist_ok=True)
    fee = fees()
    m, t = kalshi()
    fd = odds()
    rows = join(m, fd)
    rows["fee_rate"] = rows.series.map(fee)
    d = pd.concat([quotes(rows, t, tau) for tau in (5, 30)], ignore_index=True)
    d.to_parquet(OUT / "dataset.parquet", index=False)
    ev = m.event_ticker.nunique()
    print(
        f"Kalshi events {ev:,}; joined {rows.event.nunique():,} "
        f"({rows.event.nunique() / ev:.0%}); result agrees with FTR on "
        f"{(rows.y == rows.ftr_check).mean():.1%} of markets; fee rates {sorted(set(fee.values()))}"
    )


def takers(d, bench, edge):
    out = []
    for side in ("yes", "no"):
        p = d[f"ask_{side}"]
        q = d[bench] if side == "yes" else 1 - d[bench]
        fee = d.fee_rate * p * (1 - p)
        sel = (q - p - fee > edge) & p.notna() & q.notna()
        y = d.y[sel] if side == "yes" else 1 - d.y[sel]
        out.append(
            d[sel].assign(side=side, count=1.0, cost=p[sel] + fee[sel], pnl=y - p[sel] - fee[sel])
        )
    return pd.concat(out)


def calibration(d):
    mid = (d.ask_yes + (1 - d.ask_no)) / 2
    x = d.assign(mid=mid.fillna(d["last"])).dropna(subset=["mid", "bfe", "avg"])
    eps = 1e-3

    def ll(p):
        p = p.clip(eps, 1 - eps)
        return -(x.y * np.log(p) + (1 - x.y) * np.log(1 - p)).mean()

    for name in ("mid", "bfe", "avg"):
        print(
            f"    {name:4s}: Brier {((x[name] - x.y) ** 2).mean():.4f}  log-loss {ll(x[name]):.4f}"
        )
    print(f"    n={len(x):,} markets, mean |Kalshi − BFEC| {(x.mid - x.bfe).abs().mean():.4f}")


def data():
    d = pd.read_parquet(OUT / "dataset.parquet")
    return d[d.kickoff < SPLIT], d[d.kickoff >= SPLIT]


def explore():
    e, h = data()
    print(
        f"exploration {e.event.nunique():,} matches {e.kickoff.min():%Y-%m-%d} → "
        f"{e.kickoff.max():%Y-%m-%d}; holdout {h.event.nunique():,} matches (untouched)"
    )
    for tau, g in e.groupby("tau"):
        yes, no = g.ask_yes.notna().mean(), g.ask_no.notna().mean()
        print(f"τ = {tau} min: quoted YES {yes:.0%}, NO {no:.0%}")
        calibration(g)
        for bench in ("bfe", "avg"):
            for edge in (0.0, 0.02, 0.05):
                f = takers(g, bench, edge)
                print(f"  {bench} edge {edge:.2f}: {kx.fmt(kx.ratio_boot(f, 'event'))}")


def holdout(tau, edge, bench, alpha):
    _, h = data()
    g = h[h.tau == tau]
    f = takers(g, bench, edge)
    print(
        f"HOLDOUT S5 τ={tau} {bench} edge {edge}: {g.event.nunique()} matches "
        f"{g.kickoff.min():%Y-%m-%d} → {g.kickoff.max():%Y-%m-%d}: "
        f"{kx.fmt(kx.ratio_boot(f, 'event', alpha=alpha))}"
    )
    calibration(g)


def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("mode", choices=["build", "explore", "holdout"])
    ap.add_argument("--tau", type=int, default=5)
    ap.add_argument("--edge", type=float, default=0.0)
    ap.add_argument("--bench", choices=["bfe", "avg"], default="bfe")
    ap.add_argument("--alpha", type=float, default=0.05)
    a = ap.parse_args()
    if a.mode == "build":
        build()
    elif a.mode == "explore":
        explore()
    else:
        holdout(a.tau, a.edge, a.bench, a.alpha)


if __name__ == "__main__":
    main()
