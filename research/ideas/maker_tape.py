"""Idea 3b: maker version of the favourite-longshot trade, with real trades. World Cup markets: at
t0 = end_date - h, rest a bid at the YES best bid (buys YES) and an offer at the YES best ask
(sells YES = buys NO at 1 - ask). Orders are cancelled at end_date (kick-off for matches; no
in-play fills). An order counts as filled only if a trade prints *through* its price before then;
trades on the NO token are mapped to YES prices (1 - price), since Polymarket matches YES bids
against NO buys. Filled orders are held to resolution; makers pay no fee.

Usage: python -I maker_tape.py explore
       python -I maker_tape.py holdout --horizon H --side S --lo X --hi Y
"""

import argparse
import pathlib
import sys

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common as C

TD = pd.Timedelta
HORIZONS = {"7d": TD("7D"), "1d": TD("1D"), "6h": TD("6h"), "1h": TD("1h")}


def yes_prices(m):
    root = next(C.RAW.glob("kaggle_madrading__*"))
    rm = pd.read_csv(
        root / "reference_markets.csv", dtype={"yes_token_id": str, "no_token_id": str}
    )
    tok = (
        m[["mkey", "market_id"]]
        .assign(market_id=lambda d: d.market_id.astype(str))
        .merge(
            rm.assign(market_id=rm.market_id.astype(str))[
                ["market_id", "yes_token_id", "no_token_id"]
            ],
            on="market_id",
        )
    )
    ymap = dict(zip(tok.yes_token_id, tok.mkey, strict=True))
    nmap = dict(zip(tok.no_token_id, tok.mkey, strict=True))
    pf = pq.ParquetFile(root / "trades.parquet")
    out = []
    for i in range(pf.num_row_groups):
        t = pf.read_row_group(i, columns=["ts_src_ms", "token_id", "price", "size"]).to_pandas()
        is_y = t.token_id.isin(ymap.keys())
        is_n = t.token_id.isin(nmap.keys())
        t = t[is_y | is_n]
        out.append(
            pd.DataFrame(
                {
                    "mkey": np.where(
                        t.token_id.isin(ymap.keys()), t.token_id.map(ymap), t.token_id.map(nmap)
                    ),
                    "ts": pd.to_datetime(t.ts_src_ms, unit="ms", utc=True),
                    "p_yes": np.where(t.token_id.isin(ymap.keys()), t.price, 1 - t.price),
                    "size": t["size"],
                }
            )
        )
    return pd.concat(out, ignore_index=True)


def load():
    m, cut = C.markets("worldcup")
    snap = C.P.sample(
        m,
        C.quotes("worldcup", columns=["mkey", "token", "ts", "bid", "ask"]),
        "close_ts",
        {h: -d for h, d in HORIZONS.items()},
        TD("6h"),
        before="resolved_ts",
        tokens=(0,),
    )
    snap = snap[
        (snap.bid > 0) & (snap.ask < 1) & (snap.bid < snap.ask) & (snap.ask - snap.bid <= 0.05)
    ]
    tr = yes_prices(m)
    tr = tr.merge(m[["mkey", "close_ts"]], on="mkey")
    tr = tr[tr.ts < tr.close_ts]
    rows = []
    for h in HORIZONS:
        s = snap[snap.horizon == h][["mkey", "t"]]
        w = tr.merge(s, on="mkey")
        w = w[w.ts > w.t]
        g = w.groupby("mkey").p_yes.agg(min_after="min", max_after="max", n_trades="size")
        rows.append(g.reset_index().assign(horizon=h))
    g = pd.concat(rows, ignore_index=True)
    snap = snap.merge(g, on=["mkey", "horizon"], how="left")
    snap["mid"] = (snap.bid + snap.ask) / 2
    snap["cluster"] = snap.event_id
    print(f"trades on kept markets: {len(tr):,}; split at close {cut}")
    return snap


def orders(s, side, lo, hi):
    """side 'yes': bid at YES bid, filled if a later trade < bid. side 'no': offer at YES ask,
    filled if a later trade > ask; that position is long NO at 1 - ask."""
    sel = s[(s.mid >= lo) & (s.mid < hi)]
    if side == "yes":
        f = sel[sel.min_after < sel.bid].assign(price=lambda d: d.bid, y=lambda d: d.y)
        taker_px, taker_y = sel.ask, sel.y
    else:
        f = sel[sel.max_after > sel.ask].assign(price=lambda d: 1 - d.ask, y=lambda d: 1 - d.y)
        taker_px, taker_y = 1 - sel.bid, 1 - sel.y
    f = f.assign(ret=(f.y - f.price) / f.price)
    fee = C.poly_fee(taker_px)
    taker = sel.assign(ret=(taker_y - taker_px - fee) / (taker_px + fee))
    return sel, f, taker


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["explore", "holdout"])
    ap.add_argument("--horizon")
    ap.add_argument("--side")
    ap.add_argument("--lo", type=float)
    ap.add_argument("--hi", type=float)
    ap.add_argument("--alpha", type=float, default=0.05)
    a = ap.parse_args()
    s = load()
    if a.mode == "holdout":
        h = s[(s.split == "holdout") & (s.horizon == a.horizon)]
        sel, f, _t = orders(h, a.side, a.lo, a.hi)
        print(
            f"HOLDOUT maker {a.side} {a.horizon} mid in [{a.lo},{a.hi}): orders {len(sel)}, "
            f"fill rate "
            f"{len(f) / max(len(sel), 1):.3f}, {C.fmt(C.summarise(f, alpha=a.alpha))}"
        )
        return
    e = s[s.split == "explore"]
    rows = []
    bands = ((0.01, 0.1), (0.1, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 0.99))
    for h in HORIZONS:
        for side in ("yes", "no"):
            for lo, hi in bands:
                sel, f, taker = orders(e[e.horizon == h], side, lo, hi)
                sm = C.summarise(f)
                rows.append(
                    {
                        "horizon": h,
                        "buy": side,
                        "yes_mid": f"[{lo},{hi})",
                        "orders": len(sel),
                        "fill_rate": len(f) / max(len(sel), 1),
                        "fills": sm.get("n", 0),
                        "events": sm.get("clusters"),
                        "maker_ret": sm.get("ret"),
                        "lo": sm.get("lo"),
                        "hi": sm.get("hi"),
                        "taker_ret": C.summarise(taker).get("ret"),
                    }
                )
    print("exploration: maker fills on trade-through, cancelled at end_date, held to resolution")
    print(pd.DataFrame(rows).to_string(index=False, float_format=lambda v: f"{v:.4f}"))


if __name__ == "__main__":
    main()
