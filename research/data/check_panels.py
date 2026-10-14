"""Check the panels against the favourite–longshot study: rebuild each research/flb loader's
observation table from panels + sample() with that loader's filters, and compare it row for row
with the loader's own output.

Usage: python -I check_panels.py <panel_root> <flb_out_dir>
"""

import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import panels as P

root, flb = sys.argv[1:3]
TD = pd.Timedelta
KEY = ["horizon", "market_id", "side"]


def mid_or_nan(bid, ask):
    return ((bid + ask) / 2).where((bid > 0) & (ask < 1))


def rows(snap, side, p_buy, p_cal, y, **extra):
    return pd.DataFrame(
        {
            "horizon": snap.horizon.to_numpy(),
            "market_id": snap.market_id.to_numpy(),
            "side": side,
            "p_buy": np.asarray(p_buy, float),
            "p_cal": np.asarray(p_cal, float),
            "y": np.asarray(y, float),
            **extra,
        }
    )


def pancake():
    m = P.load_markets(root, "pancake")
    q = pd.concat(P.quote_parts(root, "pancake"))
    flat = q.groupby("mkey").price.nunique() <= 1
    m = m[~m.mkey.isin(flat[flat].index)]
    m = m.assign(
        anchor_min=m.resolved_ts.where(
            m.close_ts.isna() | (m.resolved_ts <= m.close_ts), m.close_ts
        )
    )
    off = {"30d": -TD(days=30), "7d": -TD(days=7), "1d": -TD(days=1)}
    out = []
    for name, col, before in (
        ("anchor_planned", "close_ts", "resolved_ts"),
        ("anchor_min", "anchor_min", None),
        ("anchor_resolved", "resolved_ts", None),
    ):
        s = P.sample(m, q, col, off, TD(days=2.5), before=before)
        s = s.assign(horizon=name + "|" + s.horizon)
        out.append(
            rows(
                s,
                np.where(s.token == 0, s.label_first, s.label_second),
                s.price,
                s.price,
                s.y_token,
            )
        )
    return pd.concat(out)


def kalshi():
    m = P.load_markets(root, "kalshi")
    off = {h: -TD(minutes=int(h[:-1])) for h in ("14m", "10m", "5m", "2m", "1m")}
    s = P.sample(m, P.quote_parts(root, "kalshi"), "close_ts", off, TD(0), tokens=(0,))
    mid = mid_or_nan(s.bid, s.ask)
    yes, no = s[(s.ask > 0) & (s.ask < 1)], s[(s.bid > 0) & (s.bid < 1)]
    return pd.concat(
        [
            rows(yes, "YES", yes.ask, mid[yes.index], yes.y),
            rows(no, "NO", 1 - no.bid, 1 - mid[no.index], 1 - no.y),
        ]
    )


def marketlens():
    m = P.load_markets(root, "marketlens")
    off = {f"open+{k}m": TD(minutes=k) for k in (5, 30, 90)}
    s = P.sample(
        m,
        P.quote_parts(root, "marketlens"),
        "data_start_ts",
        off,
        TD(minutes=15),
        before="resolved_ts",
        tokens=(0,),
    )
    c = P.contracts(s, max_spread=0.05)
    return rows(c, np.where(c.token == 0, "first", "second"), c.p_buy, c.p_cal, c.y)


def kachoio():
    m = P.load_markets(root, "kachoio")
    off = {h: -TD(seconds=v) for h, v in {"4m": 240, "2m": 120, "1m": 60, "30s": 30}.items()}
    s = P.sample(m, P.quote_parts(root, "kachoio"), "close_ts", off, TD(seconds=5))
    w = s.pivot_table(
        index=["mkey", "horizon"], columns="token", values=["bid", "ask"], dropna=False
    )
    w.columns = [f"{a}{b}" for a, b in w.columns]
    w = w.reset_index().merge(m[["mkey", "market_id", "y"]], on="mkey")
    w = w[~(w.bid0 > w.ask0)]
    mid = mid_or_nan(w.bid0, w.ask0)
    up = w[(w.ask0 > 0) & (w.ask0 < 1) & (w.ask0 - w.bid0 <= 0.05)]
    dn = w[(w.ask1 > 0) & (w.ask1 < 1) & (w.ask1 - w.bid1 <= 0.05)]
    return pd.concat(
        [
            rows(up, "Up", up.ask0, mid[up.index], up.y),
            rows(dn, "Down", dn.ask1, 1 - mid[dn.index], 1 - dn.y),
        ]
    )


def polyorderbooks():
    m = P.load_markets(root, "polyorderbooks")
    hz = {
        "5m": {"4m": 240, "2m": 120, "1m": 60, "30s": 30},
        "15m": {"14m": 840, "10m": 600, "5m": 300, "1m": 60},
        "4h": {"3h": 10800, "1h": 3600, "30m": 1800, "5m": 300},
    }
    out = []
    for length, h in hz.items():
        q = next(P.quote_parts(root, "polyorderbooks", parts=[length]))
        q = q[q.bid.notna() & q.ask.notna() & (q.bid < q.ask)]
        s = P.sample(
            m[m.length == length],
            q,
            "close_ts",
            {f"{length}:{k}": -TD(seconds=v) for k, v in h.items()},
            TD(seconds=15),
        )
        s = s[(s.ask > 0) & (s.ask < 1) & (s.ask - s.bid <= 0.05)]
        out.append(
            rows(
                s,
                np.where(s.token == 0, "Up", "Down"),
                s.ask,
                ((s.bid + s.ask) / 2).where(s.bid > 0),
                s.y_token,
            )
        )
    return pd.concat(out)


def worldcup():
    m = P.load_markets(root, "worldcup")
    off = {"7d": -TD(days=7), "1d": -TD(days=1), "1h": -TD(hours=1)}
    s = P.sample(
        m,
        P.quote_parts(root, "worldcup", columns=["mkey", "token", "ts", "bid", "ask"]),
        "close_ts",
        off,
        TD(hours=6),
        before="resolved_ts",
        tokens=(0,),
    )
    c = P.contracts(s, max_spread=0.05)
    return rows(c, np.where(c.token == 0, "first", "second"), c.p_buy, c.p_cal, c.y)


def compare(name, mine, theirs):
    if name == "pancake":
        theirs = theirs.assign(horizon=theirs.anchor + "|" + theirs.horizon)
    theirs = theirs.assign(market_id=theirs.market_id.astype(str))[KEY + ["p_buy", "p_cal", "y"]]
    mine = mine.assign(market_id=mine.market_id.astype(str))
    j = mine.merge(theirs, on=KEY, how="outer", suffixes=("", "_flb"), indicator=True)
    both = j[j._merge == "both"]
    diff = {
        c: int(
            (
                ~np.isclose(
                    both[c].astype(float),
                    both[f"{c}_flb"].astype(float),
                    rtol=0,
                    atol=1e-12,
                    equal_nan=True,
                )
            ).sum()
        )
        for c in ("p_buy", "p_cal", "y")
    }
    res = {
        "rows_panel": len(mine),
        "rows_flb": len(theirs),
        "only_panel": int((j._merge == "left_only").sum()),
        "only_flb": int((j._merge == "right_only").sum()),
        "dup_keys_flb": int(theirs.duplicated(KEY).sum()),
        **{f"diff_{k}": v for k, v in diff.items()},
    }
    ok = (
        res["only_panel"] == res["only_flb"] == 0
        and not any(diff.values())
        and len(mine) == len(theirs)
    )
    print(f"{'OK  ' if ok else 'FAIL'} {name:15s} {res}")
    return ok


CHECKS = {
    "pancake": pancake,
    "kalshi": kalshi,
    "marketlens": marketlens,
    "kachoio": kachoio,
    "polyorderbooks": polyorderbooks,
    "worldcup": worldcup,
}
names = sys.argv[3:] or list(CHECKS)
results = [compare(n, CHECKS[n](), pd.read_parquet(f"{flb}/obs_{n}.parquet")) for n in names]
sys.exit(0 if all(results) else 1)
