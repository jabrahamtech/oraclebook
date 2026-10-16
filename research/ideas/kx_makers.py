"""Round 2: who earns on Kalshi, makers or takers, by price and series?

c1      the pre-registered confirmatory test (PROTOCOL_V2.md): sports trades 28 -> 4 h before the
        event in which the taker bought the side priced <= 10c; the maker is long the other side.
        Dollar-weighted maker return net of the series' maker fee, CI resampling events;
        exploration and holdout both reported, the decision uses the holdout.
map     exploration only: maker and taker returns by taker price band, per series.

Usage: python -I kx_makers.py c1
       python -I kx_makers.py map <series,...>
"""

import argparse
import pathlib
import sys

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import kx

SPORTS = [
    "KXNFLGAME",
    "KXNCAAFGAME",
    "KXEPLGAME",
    "KXLALIGAGAME",
    "KXSERIEAGAME",
    "KXBUNDESLIGAGAME",
    "KXUCLGAME",
    "KXMLSGAME",
    "KXMLBGAME",
    "KXNHLGAME",
    "KXWNBAGAME",
    "KXUFCFIGHT",
    "KXATPMATCH",
    "KXWTAMATCH",
    "KXMLBRFI",
]
BANDS = [0, 0.05, 0.10, 0.20, 0.35, 0.50, 0.65, 0.80, 0.90, 0.95, 1.0]


def c1():
    have = [s for s in SPORTS if s in kx.available()]
    _m, t = kx.load(have)
    t = kx.sides(t)
    occ = t.occurrence_datetime.fillna(t.close_time)
    t = t[(t.ts < occ - pd.Timedelta(hours=4)) & (t.ts >= occ - pd.Timedelta(hours=12))]
    long_shot = t[(t.p_taker > 0) & (t.p_taker <= 0.10)]
    d = kx.pnl(long_shot, "maker")
    print(f"C1 series with data: {have}")
    for split in ("explore", "holdout"):
        s = kx.ratio_boot(d[d.split == split], "event_ticker")
        print(f"C1 {split:8s} maker long the favourite vs longshot takers: {kx.fmt(s)}")
    per = d.groupby(["series", "split"]).apply(
        lambda g: pd.Series({"trades": len(g), "ret": g.pnl.sum() / g.cost.sum()}),
        include_groups=False,
    )
    print(per.unstack("split").round(4).to_string())


def band_table(t, who):
    t = kx.pnl(t, who)
    t["band"] = pd.cut(t[f"p_{who}"], BANDS, include_lowest=True)
    rows = []
    for b, g in t.groupby("band", observed=True):
        s = kx.ratio_boot(g, "event_ticker", b=1000)
        rows.append(
            {
                "band": str(b),
                "trades": s["n"],
                "dollars": s.get("dollars"),
                "ret": s["ret"],
                "lo": s["lo"],
                "hi": s["hi"],
            }
        )
    return pd.DataFrame(rows)


def mapping(series):
    _m, t = kx.load(series)
    t = kx.sides(t[t.split == "explore"])
    for s, g in t.groupby("series"):
        print(f"\n{s}: {len(g):,} trades, {g.event_ticker.nunique()} events (exploration)")
        tk = band_table(g, "taker").set_index("band")
        mk = band_table(g, "maker").set_index("band")
        out = pd.concat(
            {"taker": tk[["trades", "ret", "lo", "hi"]], "maker": mk[["ret", "lo", "hi"]]}, axis=1
        )
        print(out.to_string(float_format=lambda v: f"{v:.4f}"))
        print(
            "bands are the price of the side each party holds; "
            "taker fee 0.07·m, maker fee per series"
        )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["c1", "map"])
    ap.add_argument("series", nargs="?")
    a = ap.parse_args()
    if a.mode == "c1":
        c1()
    else:
        mapping(a.series.split(",") if a.series else kx.available())


if __name__ == "__main__":
    main()
