"""Common market/quote panels for the research datasets, plus a point-in-time sampler.

Each dataset becomes two tables under <panel_root>/<name>/:

markets.parquet: one row per resolved binary market (or one with an inferred outcome)
  mkey         int32 join key into quotes
  market_id    the source's market id;  event_id  default cluster for event-level resampling
  label_first, label_second   outcome labels; token 0 is the first
  open_ts, close_ts, resolved_ts   listing time, *scheduled* close/end, actual resolution
                                   (UTC; NaT where the source does not give one)
  y            payout of the first token: 1, 0, or NaN where the outcome is unknown
  y_official   True when y comes from the venue's resolution, False when inferred
  plus dataset-specific columns (coin, length, category, question, ...)

quotes/<part>.parquet: quote history, one file per part (coin, contract length, folder, ...)
  mkey, token  0 = first outcome, 1 = second (only 0 where the source has one book)
  ts           time the quote describes;  known_ts  when a trader could first see it
  bid, ask     best bid / ask (NaN where absent);  price  last/reported price where there is
               no book (NaN otherwise); plus dataset-specific columns

Filters that are methodology choices (spread caps, crossed books, staleness, horizons, clusters)
are left to the caller: the panels keep every quote the source has for the markets they keep.

Usage: python -I panels.py <raw_root> <panel_root> [name ...]
"""

import gzip
import json
import pathlib
import shutil
import sys

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

HERE = pathlib.Path(__file__).resolve().parent
UTC = "datetime64[us, UTC]"
MARKET_COLS = [
    "mkey",
    "market_id",
    "event_id",
    "label_first",
    "label_second",
    "open_ts",
    "close_ts",
    "resolved_ts",
    "y",
    "y_official",
]


def _ts(s, unit=None):
    if unit:
        return pd.to_datetime(s, unit=unit, utc=True).astype(UTC)
    return pd.to_datetime(s, utc=True, format="mixed").astype(UTC)


def _finish(m, **defaults):
    for c in ("open_ts", "close_ts", "resolved_ts"):
        if c not in m:
            m[c] = pd.Series(pd.NaT, index=m.index, dtype=UTC)
        m[c] = m[c].astype(UTC)
    for k, v in defaults.items():
        if k not in m:
            m[k] = v
    m["market_id"] = m.market_id.astype(str)
    m["event_id"] = m.event_id.astype(str)
    m["y"] = m.y.astype(float)
    extra = [c for c in m.columns if c not in MARKET_COLS]
    return m[MARKET_COLS + extra].reset_index(drop=True)


def _quotes(q):
    cols = ["mkey", "token", "ts", "known_ts", "bid", "ask", "price"]
    q = q.assign(mkey=q.mkey.astype("int32"), token=q.token.astype("int8"), ts=q.ts.astype(UTC))
    for c in ("bid", "ask", "price"):
        if c in q:
            q[c] = q[c].astype(float)
    if "known_ts" in q:
        q["known_ts"] = q.known_ts.astype(UTC)
    keep = [c for c in cols if c in q] + [c for c in q.columns if c not in cols]
    sort = "known_ts" if "known_ts" in q else "ts"
    return q[keep].sort_values([sort, "mkey", "token"], kind="stable").reset_index(drop=True)


# --- builders: build_<name>(raw_dir, put) -> (markets, exclusions); put(part, quotes) writes a part


def build_pancake(raw, put):
    root = raw / "github_pancake/datasets/v1/polymarket"

    def load(t, cols):
        fs = sorted(root.glob(f"{t}/*/*/*.parquet"))
        return pd.concat([pd.read_parquet(f, columns=cols) for f in fs], ignore_index=True)

    m = load(
        "markets",
        [
            "market_id",
            "title",
            "category",
            "market_kind",
            "resolves_at_planned",
            "total_volume_usd",
        ],
    )
    o = load("outcomes", ["instrument_id", "market_id", "label", "payoff_on_resolve", "sort_order"])
    r = load("resolutions", ["market_id", "resolved_at", "winning_outcome_instrument_id"])
    c = load("candles_1d", ["instrument_id", "observed_at", "released_at", "close"])
    ex = {"markets_total": len(m), "resolved_markets": int(r.market_id.nunique())}
    res = r.merge(m, on="market_id", how="left")
    res = res[
        (res.market_kind == "binary") & (res.market_id.map(o.groupby("market_id").size()) == 2)
    ]
    ex["resolved_binary_2leg"] = len(res)
    legs = o[o.market_id.isin(res.market_id)].copy()
    win = res.set_index("market_id").winning_outcome_instrument_id
    legs["y"] = (legs.instrument_id == legs.market_id.map(win)).astype(int)
    mismatch = (legs.y != legs.payoff_on_resolve.round().astype(int)).groupby(legs.market_id).any()
    bad = legs.groupby("market_id").payoff_on_resolve.apply(
        lambda s: sorted(s.round(6)) != [0.0, 1.0]
    )
    ex["payoff_vs_resolution_mismatch"] = int(mismatch.sum())
    ex["non_0_1_payoff_(void/50-50)"] = int(bad.sum())
    drop = set(mismatch[mismatch].index) | set(bad[bad].index)
    res = res[~res.market_id.isin(drop)]
    legs = legs[legs.market_id.isin(res.market_id)]
    c = c[c.instrument_id.isin(legs.instrument_id)]
    has_c = set(legs.market_id[legs.instrument_id.isin(set(c.instrument_id))])
    ex["no_candles"] = int((~res.market_id.isin(has_c)).sum())
    res = res[res.market_id.isin(has_c)].sort_values("market_id").reset_index(drop=True)
    legs = legs[legs.market_id.isin(res.market_id)].sort_values(
        ["market_id", "sort_order", "instrument_id"]
    )
    legs["token"] = legs.groupby("market_id").cumcount()
    res["mkey"] = np.arange(len(res))
    legs["mkey"] = legs.market_id.map(res.set_index("market_id").mkey)
    first = legs[legs.token == 0].set_index("market_id")
    second = legs[legs.token == 1].set_index("market_id")
    planned = pd.to_datetime(res.resolves_at_planned, errors="coerce", utc=True)
    ex["planned_end_missing"] = int(planned.isna().sum())
    resolved = _ts(res.resolved_at, "s")
    # Event = category x the day the market ended (earlier of planned and actual end).
    day = resolved.where(planned.isna() | (resolved <= planned), planned).dt.strftime("%Y-%m-%d")
    mk = pd.DataFrame(
        {
            "mkey": res.mkey,
            "market_id": res.market_id,
            "event_id": res.category.fillna("na") + "|" + day,
            "label_first": res.market_id.map(first.label),
            "label_second": res.market_id.map(second.label),
            "close_ts": planned,
            "resolved_ts": resolved,
            "y": res.market_id.map(first.y),
            "y_official": True,
            "category": res.category,
            "title": res.title,
            "volume_usd": res.total_volume_usd,
        }
    )
    q = c.merge(legs[["instrument_id", "mkey", "token"]], on="instrument_id")
    put(
        "candles_1d",
        _quotes(
            pd.DataFrame(
                {
                    "mkey": q.mkey,
                    "token": q.token,
                    "ts": _ts(q.observed_at, "s"),
                    "known_ts": _ts(q.released_at, "s"),
                    "price": q.close,
                }
            )
        ),
    )
    return _finish(mk), ex


def build_kalshi(raw, put):
    d = raw / "github_kalshi/data/raw/kalshi_btc15"

    def q(c, k):
        v = c.get(k, c.get(f"{k}_dollars"))
        return np.nan if v is None else float(v)

    listed = {}
    with gzip.open(d / "live_markets.jsonl.gz", "rt") as f:
        for line in f:
            for mk in json.loads(line)["response"]["markets"]:
                listed[mk["ticker"]] = mk
    ex = {"markets_listed": len(listed), "no_candles": 0, "non_yes_no_result": 0}
    rows, quotes = [], []
    with gzip.open(d / "candlesticks.jsonl.gz", "rt") as f:
        for line in f:
            c = json.loads(line)
            mk = listed.get(c["endpoint"].split("/")[-2])
            if mk is None:
                continue
            if mk["result"] not in ("yes", "no"):
                ex["non_yes_no_result"] += 1
                continue
            cs = c["response"]["candlesticks"]
            if not cs:
                ex["no_candles"] += 1
                continue
            k = len(rows)
            rows.append(
                {
                    "mkey": k,
                    "market_id": mk["ticker"],
                    "event_id": mk["close_time"][:10],
                    "label_first": "YES",
                    "label_second": "NO",
                    "open_ts": mk.get("open_time"),
                    "close_ts": mk["close_time"],
                    "resolved_ts": mk.get("settlement_ts"),
                    "y": 1 if mk["result"] == "yes" else 0,
                    "y_official": True,
                    "event_ticker": mk.get("event_ticker"),
                }
            )
            quotes += [
                (
                    k,
                    x["end_period_ts"],
                    q(x["yes_bid"], "close"),
                    q(x["yes_ask"], "close"),
                    q(x.get("price") or {}, "close"),
                )
                for x in cs
            ]
    m = pd.DataFrame(rows)
    for col in ("open_ts", "close_ts", "resolved_ts"):
        m[col] = _ts(m[col])
    qd = pd.DataFrame(quotes, columns=["mkey", "end_period_ts", "bid", "ask", "price"])
    # A candle's close quote is the book at the end of its minute.
    qd = qd.drop_duplicates(["mkey", "end_period_ts"], keep="last")
    put(
        "candles_1m",
        _quotes(qd.assign(token=0, ts=_ts(qd.end_period_ts, "s")).drop(columns="end_period_ts")),
    )
    ex["markets_kept"] = len(m)
    return _finish(m), ex


def build_marketlens(raw, put):
    root = raw / "github_marketlens"
    ms, ex, k0 = [], {}, 0
    for d in sorted(p for p in root.iterdir() if (p / "markets.csv").exists()):
        m = pd.read_csv(d / "markets.csv")
        outs = m.outcomes.str.split("|")
        first, second = outs.str[0], outs.str[1]
        valid = (outs.str.len() == 2) & (m.winning_outcome.eq(first) | m.winning_outcome.eq(second))
        ex[d.name] = {"markets": len(m), "excluded_void_or_nonbinary": int((~valid).sum())}
        m = m[valid].reset_index(drop=True)
        m = pd.DataFrame(
            {
                "mkey": k0 + np.arange(len(m)),
                "market_id": m.market_id,
                "event_id": d.name + "|" + m.event,
                "label_first": first[valid].to_numpy(),
                "label_second": second[valid].to_numpy(),
                "open_ts": _ts(m.open_time),
                "close_ts": _ts(m.close_time),
                "resolved_ts": _ts(m.resolved_at),
                "y": (m.winning_outcome.to_numpy() == first[valid].to_numpy()).astype(int),
                "y_official": True,
                "folder": d.name,
                "series": m.series,
                "subtype": m.subtype,
                "question": m.question,
                "volume_usd": m.volume_usd,
                "data_start_ts": _ts(m.data_start),
            }
        )
        k0 += len(m)
        s = pd.read_csv(d / "snapshots.csv", usecols=["market_id", "t_ms", "best_bid", "best_ask"])
        s = s.merge(m[["market_id", "mkey"]], on="market_id")
        put(
            d.name,
            _quotes(
                pd.DataFrame(
                    {
                        "mkey": s.mkey,
                        "token": 0,
                        "ts": _ts(s.t_ms, "ms"),
                        "bid": s.best_bid,
                        "ask": s.best_ask,
                    }
                )
            ),
        )
        ms.append(m)
    return _finish(pd.concat(ms, ignore_index=True)), ex


def build_kachoio(raw, put):
    root = next(raw.glob("kaggle_kachoio__*"))
    ms, ex, k0 = [], {}, 0
    for mf in sorted(root.glob("*_markets.parquet")):
        coin = mf.name.split("_")[0]
        m = pd.read_parquet(mf).reset_index(drop=True)
        ex[coin] = {"markets": len(m), "outcome_null": int((~m.outcome.isin(["Up", "Down"])).sum())}
        m = pd.DataFrame(
            {
                "mkey": k0 + np.arange(len(m)),
                "market_id": m.condition_id,
                "event_id": m.market_end.dt.strftime("%Y-%m-%d"),
                "label_first": "Up",
                "label_second": "Down",
                "open_ts": m.market_start,
                "close_ts": m.market_end,
                "y": m.outcome.map({"Up": 1.0, "Down": 0.0}),
                "y_official": False,
                "coin": coin,
                "slug": m.slug,
                "volume": m.volume,
            }
        )
        k0 += len(m)
        t = pd.read_parquet(
            root / f"{coin}_ticks.parquet", columns=["condition_id", "t", "bu", "au", "bd", "ad"]
        )
        t = t.merge(m[["market_id", "mkey"]], left_on="condition_id", right_on="market_id")
        ts = _ts(t.t, "s")
        put(
            coin,
            _quotes(
                pd.concat(
                    [
                        pd.DataFrame(
                            {"mkey": t.mkey, "token": 0, "ts": ts, "bid": t.bu, "ask": t.au}
                        ),
                        pd.DataFrame(
                            {"mkey": t.mkey, "token": 1, "ts": ts, "bid": t.bd, "ask": t.ad}
                        ),
                    ]
                )
            ),
        )
        ms.append(m)
    return _finish(pd.concat(ms, ignore_index=True)), ex


def build_polyorderbooks(raw, put):
    root = next(raw.glob("kaggle_polyorderbooks__*"))
    ms, ex, k0 = [], {}, 0
    for length in ("5m", "15m", "4h"):
        x = pd.read_parquet(
            root / f"updown_{length}.parquet",
            columns=[
                "market_slug",
                "coin",
                "outcome",
                "is_winning_outcome",
                "market_end_at",
                "captured_at",
                "best_bid",
                "best_ask",
            ],
        )
        per = x.drop_duplicates(["market_slug", "outcome"]).pivot(
            index="market_slug", columns="outcome", values="is_winning_outcome"
        )
        ok = per.notna().all(axis=1) & (per.Up != per.Down)
        ex[length] = {
            "markets": len(per),
            "excluded_missing_side_or_inconsistent_winner": int((~ok).sum()),
        }
        m = (
            x[x.market_slug.isin(per.index[ok])]
            .drop_duplicates("market_slug")
            .sort_values("market_slug")
            .reset_index(drop=True)
        )
        m = pd.DataFrame(
            {
                "mkey": k0 + np.arange(len(m)),
                "market_id": m.market_slug,
                "event_id": m.market_end_at.dt.strftime("%Y-%m-%d %H"),
                "label_first": "Up",
                "label_second": "Down",
                "close_ts": m.market_end_at,
                "y": m.market_slug.map(per.Up).astype(int),
                "y_official": True,
                "coin": m.coin,
                "length": length,
            }
        )
        k0 += len(m)
        x = x.merge(m[["market_id", "mkey"]], left_on="market_slug", right_on="market_id")
        put(
            length,
            _quotes(
                pd.DataFrame(
                    {
                        "mkey": x.mkey,
                        "token": (x.outcome == "Down").astype(int),
                        "ts": x.captured_at,
                        "bid": x.best_bid,
                        "ask": x.best_ask,
                    }
                )
            ),
        )
        ms.append(m)
    return _finish(pd.concat(ms, ignore_index=True)), ex


def build_worldcup(raw, put, rows_per_part=10_000_000):
    root = next(raw.glob("kaggle_madrading__*"))
    o = pd.read_csv(root / "outcomes.csv")
    rm = pd.read_csv(root / "reference_markets.csv", dtype={"yes_token_id": str})
    ex = {"markets": len(o)}
    labels = o.outcome_labels.map(lambda s: json.loads(s) if isinstance(s, str) else [])
    prices = o.outcome_prices.map(
        lambda s: sorted(float(v) for v in json.loads(s)) if isinstance(s, str) else []
    )
    ok = (
        o.is_resolved
        & o.winning_index.isin([0, 1])
        & (labels.str.len() == 2)
        & prices.map(lambda p: p == [0.0, 1.0])
    )
    ex["unresolved_void_or_nonbinary"] = int((~ok).sum())
    o = o[ok].assign(label_first=labels[ok].str[0], label_second=labels[ok].str[1])
    # yes_token_id is the first listed outcome's token (tokens are labelled YES/NO generically).
    o = o.merge(rm[["market_id", "yes_token_id"]], on="market_id", how="inner").reset_index(
        drop=True
    )
    m = pd.DataFrame(
        {
            "mkey": np.arange(len(o)),
            "market_id": o.market_id,
            "event_id": o.event_id,
            "label_first": o.label_first,
            "label_second": o.label_second,
            "close_ts": _ts(o.end_date),
            "resolved_ts": _ts(o.closed_time),
            "y": (o.winning_index == 0).astype(int),
            "y_official": True,
            "question": o.question,
            "token_id_first": o.yes_token_id,
        }
    )
    ex["markets_kept"] = len(m)
    key = m.set_index("token_id_first").mkey
    pf = pq.ParquetFile(root / "candles_1m_mid.parquet")
    buf, n, part = [], 0, 0
    for i in range(pf.num_row_groups):
        c = pf.read_row_group(
            i, columns=["token_id", "ts", "bid_close", "ask_close", "mid_close", "source"]
        ).to_pandas()
        c = c[c.token_id.isin(key.index)]
        # A bar's close quote is the book at the end of its minute.
        buf.append(
            pd.DataFrame(
                {
                    "mkey": c.token_id.map(key),
                    "token": 0,
                    "ts": c.ts + pd.Timedelta(minutes=1),
                    "bid": c.bid_close,
                    "ask": c.ask_close,
                    "mid_close": c.mid_close,
                    "source": c.source.astype("category"),
                }
            )
        )
        n += len(c)
        if n >= rows_per_part or i == pf.num_row_groups - 1:
            put(f"candles_{part:02d}", _quotes(pd.concat(buf, ignore_index=True)))
            buf, n, part = [], 0, part + 1
    return _finish(m), ex


BUILDERS = {k[6:]: v for k, v in globals().items() if k.startswith("build_")}


# --- reading and sampling


def load_markets(panel_root, name):
    return pd.read_parquet(pathlib.Path(panel_root) / name / "markets.parquet")


def quote_parts(panel_root, name, parts=None, columns=None):
    """Yield each quotes part as a DataFrame (parts: names to keep, e.g. ["btc"])."""
    for f in sorted((pathlib.Path(panel_root) / name / "quotes").glob("*.parquet")):
        if parts is None or f.stem in parts:
            yield pd.read_parquet(f, columns=columns)


def sample(markets, quotes, anchor, offsets, max_stale, before=None, tokens=(0, 1)):
    """Point-in-time quotes at anchor + offset for every market, horizon and token.

    anchor     markets column (or a Series aligned to markets) to measure from, e.g. "close_ts"
    offsets    {horizon name: Timedelta added to the anchor}; negative means before it
    max_stale  drop a sample whose latest visible quote is older than this (Timedelta)
    before     optional markets column; a sample counts only if taken strictly before it
               (e.g. "resolved_ts" so a market must still be open)
    quotes     a DataFrame or an iterable of parts (see quote_parts); the latest quote with
               known_ts (or ts) <= the sample time is used, never a later one
    Returns one row per (market, horizon, token) with t (sample time), the quote, the market's
    columns, and y_token (payout of that token).
    """
    m = markets.assign(_anchor=markets[anchor] if isinstance(anchor, str) else anchor)
    m = m[m._anchor.notna()]
    t = pd.concat(
        [m[["mkey"]].assign(horizon=h, t=(m._anchor + d).astype(UTC)) for h, d in offsets.items()],
        ignore_index=True,
    )
    if before is not None:
        lim = t.mkey.map(m.set_index("mkey")[before])
        t = t[lim.isna() | (t.t < lim)]
    t = pd.concat([t.assign(token=np.int8(k)) for k in tokens], ignore_index=True).sort_values("t")
    t["mkey"] = t.mkey.astype("int32")
    parts = [quotes] if isinstance(quotes, pd.DataFrame) else quotes
    snaps = []
    for q in parts:
        on = "known_ts" if "known_ts" in q else "ts"
        q = q[q.mkey.isin(t.mkey)]
        if q.empty:
            continue
        if not q[on].is_monotonic_increasing:
            q = q.sort_values(on, kind="stable")
        if on == "ts":
            q = q.assign(known_ts=q.ts)
        snaps.append(
            pd.merge_asof(
                t, q, left_on="t", right_on="known_ts", by=["mkey", "token"], direction="backward"
            ).dropna(subset=["known_ts"])
        )
    if not snaps:
        return pd.DataFrame()
    s = pd.concat(snaps, ignore_index=True)
    if len(snaps) > 1:
        s = s.sort_values("known_ts", kind="stable").groupby(["mkey", "horizon", "token"]).tail(1)
    s = s[(s.t - s.ts) <= max_stale]
    s = s.merge(markets, on="mkey", how="left")
    s["y_token"] = np.where(s.token == 0, s.y, 1 - s.y)
    return s.reset_index(drop=True)


def contracts(snap, max_spread=None, complement=True):
    """Turn sampled quotes into buyable contracts (dataset, horizon, market_id, event_id, side,
    p_buy, p_cal, y), the observation table research scripts share.

    Book quotes: buy at the ask; p_cal is the mid when the book is two-sided, uncrossed and inside
    (0, 1). With max_spread, a book counts only if it is all of those and ask - bid <= max_spread.
    Price-only quotes: p_buy = p_cal = price.
    complement: where a market has no token-1 quotes, buying the second outcome costs 1 - bid of the
    first token's book.
    """
    s = snap.copy()
    for col in ("bid", "ask", "price"):
        if col not in s:
            s[col] = np.nan
    book = s.ask.notna()
    inside = (s.bid > 0) & (s.ask < 1) & (s.bid <= s.ask)
    mid = ((s.bid + s.ask) / 2).where(inside)
    tight = (
        (inside & (s.ask - s.bid <= max_spread))
        if max_spread is not None
        else pd.Series(True, index=s.index)
    )
    out = []
    own = s[book & (s.ask > 0) & (s.ask < 1) & tight]
    out.append(own.assign(p_buy=own.ask, p_cal=mid[own.index], y=own.y_token))
    px = s[~book & s.price.between(0, 1, inclusive="neither")]
    out.append(px.assign(p_buy=px.price, p_cal=px.price, y=px.y_token))
    if complement:
        has_second = set(s.mkey[s.token == 1])
        c = s[(s.token == 0) & ~s.mkey.isin(has_second) & book & (s.bid > 0) & (s.bid < 1) & tight]
        out.append(
            c.assign(token=np.int8(1), p_buy=1 - c.bid, p_cal=1 - mid[c.index], y=1 - c.y_token)
        )
    o = pd.concat(out, ignore_index=True)
    o["side"] = np.where(o.token == 0, o.label_first, o.label_second)
    return o


def build(raw_root, panel_root, names):
    raw, panel_root = pathlib.Path(raw_root), pathlib.Path(panel_root)
    for name in names:
        out = panel_root / name
        if out.exists():
            shutil.rmtree(out)
        (out / "quotes").mkdir(parents=True)
        counts = {}

        def put(part, q, out=out, counts=counts):
            q.to_parquet(out / "quotes" / f"{part}.parquet", index=False)
            counts[part] = len(q)

        markets, ex = BUILDERS[name](raw, put)
        markets.to_parquet(out / "markets.parquet", index=False)
        meta = {
            "markets": len(markets),
            "markets_with_y": int(markets.y.notna().sum()),
            "quote_rows": counts,
            "close_range": [str(markets.close_ts.min()), str(markets.close_ts.max())],
            "exclusions": ex,
        }
        (out / "_panel.json").write_text(json.dumps(meta, indent=1, default=int))
        print(name, json.dumps({k: v for k, v in meta.items() if k != "exclusions"}, default=int))


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    sources = json.loads((HERE / "sources.json").read_text())
    build(sys.argv[1], sys.argv[2], sys.argv[3:] or list(sources))
