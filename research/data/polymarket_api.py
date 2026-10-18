"""Download resolved Polymarket crypto markets from the public APIs (no key needed): Gamma for
markets and resolutions, data-api for the taker tape (with wallets), CLOB prices-history for
price paths. Responses are untrusted data: parsed as JSON, never executed. Resumable and
rate-limited; prints one line per series.

Layout under <raw_root>/polymarket_api/<series>/: markets.parquet, trades.parquet,
prices.parquet, _source.json.

Usage:
  python -I polymarket_api.py <raw> updown --assets BTC,ETH --interval 5m|15m|1h|4h
         --start 2026-09-24 --end 2026-10-15 [--trades]
  python -I polymarket_api.py <raw> events --name hit --title "^What price will (Bitcoin|Ethereum)
         hit in" --start 2024-11-01 --end 2026-10-15
  python -I polymarket_api.py <raw> prices --series btc-1h,eth-1h --token up_token
         --from-col open_ts --minutes 10 --fidelity 1
"""

import argparse
import datetime as dt
import json
import pathlib
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

import pandas as pd

GAMMA = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
PAGE = 500
ET = ZoneInfo("America/New_York")
NAMES = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "xrp"}
SECONDS = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
TRADE_COLS = ["ts", "token", "side", "price", "size", "wallet", "tx"]


class Limiter:
    def __init__(self, rps):
        self.gap, self.next, self.lock = 1.0 / rps, 0.0, threading.Lock()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            t = max(now, self.next)
            self.next = t + self.gap
        time.sleep(max(0.0, t - now))


LIMIT = Limiter(8)


def get(url, tries=12):
    """JSON body, or None on 404 / a 400 (data-api answers 400 past its deepest offset)."""
    code = ""
    for i in range(tries):
        LIMIT.wait()
        r = subprocess.run(
            ["curl", "-s", "-m", "60", "-w", "\n%{http_code}", url],
            capture_output=True,
            text=True,
            check=False,
        )
        body, _, code = r.stdout.rpartition("\n")
        if code == "200":
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                pass
        if code in ("400", "404"):
            return None
        time.sleep(1.5 if code == "429" else min(30, 2**i))
    sys.exit(f"GET {url} failed after {tries} tries (last HTTP {code})")


def _list(v):
    return json.loads(v) if isinstance(v, str) else (v or [])


def resolved(m):
    """(token ids, first-outcome payout) of a resolved binary Gamma market, else None."""
    tokens, prices = _list(m.get("clobTokenIds")), [float(p) for p in _list(m.get("outcomePrices"))]
    if len(tokens) != 2 or len(prices) != 2 or prices[0] not in (0.0, 1.0) or sum(prices) != 1:
        return None
    return tokens, prices[0]


def slug(asset, interval, start):
    if interval != "1h":
        return f"{asset.lower()}-updown-{interval}-{start}"
    t = dt.datetime.fromtimestamp(start, ET)
    h = t.hour % 12 or 12
    return f"{NAMES[asset]}-up-or-down-{t:%B}-{t.day}-{h}{'am' if t.hour < 12 else 'pm'}-et".lower()


def updown_market(asset, interval, start):
    s = slug(asset, interval, start)
    found = get(f"{GAMMA}/markets?slug={s}&closed=true") or get(f"{GAMMA}/markets?slug={s}")
    if not found:
        ev = get(f"{GAMMA}/events?slug={s}")
        found = ev[0].get("markets") if ev else None
    if not found:
        return None
    m = found[0]
    if [str(o).lower() for o in _list(m.get("outcomes"))] != ["up", "down"]:
        return None
    r = resolved(m)
    if r is None:
        return None
    return {
        "condition_id": m["conditionId"], "slug": s,
        "open_ts": pd.Timestamp(start, unit="s", tz="UTC"),
        "close_ts": pd.Timestamp(start + SECONDS[interval], unit="s", tz="UTC"),
        "up_token": r[0][0], "down_token": r[0][1], "y": r[1],
    }


def starts(asset, interval, t0, t1):
    """Window starts; 4-hour alignment is probed on the first day (it may follow ET, not UTC)."""
    step = SECONDS[interval]
    if interval != "4h":
        return list(range(t0 - t0 % step, t1 - step + 1, step))
    base = t0 - t0 % 86400 + 86400
    for off in range(0, step, 3600):
        if any(updown_market(asset, interval, base + off + k * step) for k in range(3)):
            return list(range(t0 - t0 % 86400 + off, t1 - step + 1, step))
    return []


def trades(condition_id):
    """Every taker trade of a market; truncated when data-api stops before the oldest trade."""
    out, offset = [], 0
    while True:
        d = get(f"{DATA_API}/trades?market={condition_id}&limit={PAGE}&offset={offset}"
                "&takerOnly=true")
        if d is None:
            return condition_id, out, offset > 0
        out += [
            (int(t["timestamp"]), str(t["asset"]), str(t["side"]).upper(), float(t["price"]),
             float(t["size"]), str(t.get("proxyWallet", "")), str(t.get("transactionHash", "")))
            for t in d
        ]
        if len(d) < PAGE:
            return condition_id, out, False
        offset += PAGE


def _save(folder, m, **meta):
    m.to_parquet(folder / "markets.parquet", index=False)
    (folder / "_source.json").write_text(json.dumps(
        {"fetched_at": dt.datetime.now(dt.UTC).isoformat(), "markets": len(m), **meta}, indent=1))


def updown(root, assets, interval, t0, t1, threads, with_trades):
    for asset in assets:
        folder = root / f"{asset.lower()}-{interval}"
        folder.mkdir(parents=True, exist_ok=True)
        mp = folder / "markets.parquet"
        have = pd.read_parquet(mp) if mp.exists() else pd.DataFrame(columns=["slug"])
        todo = [s for s in starts(asset, interval, t0, t1)
                if slug(asset, interval, s) not in set(have.slug)]
        with ThreadPoolExecutor(threads) as ex:
            new = [r for r in ex.map(lambda s, a=asset: updown_market(a, interval, s), todo) if r]
        m = pd.concat([have, pd.DataFrame(new)], ignore_index=True)
        if m.empty:
            print(f"{folder.name}: 0 resolved markets (slug pattern not found?)")
            continue
        _save(folder, m, gamma=GAMMA, interval=interval, start=t0, end=t1)
        msg = f"{folder.name}: {len(m)} markets"
        if with_trades:
            tp = folder / "trades.parquet"
            old = pd.read_parquet(tp) if tp.exists() else pd.DataFrame(columns=["condition_id"])
            need = sorted(set(m.condition_id) - set(old.condition_id))
            parts, cut = [old], 0
            with ThreadPoolExecutor(threads) as ex:
                for i, (cid, rows, trunc) in enumerate(ex.map(trades, need), 1):
                    parts.append(pd.DataFrame(rows, columns=TRADE_COLS).assign(condition_id=cid))
                    cut += trunc
                    if i % 200 == 0:
                        pd.concat(parts, ignore_index=True).to_parquet(tp, index=False)
            t = pd.concat(parts, ignore_index=True)
            t.to_parquet(tp, index=False)
            msg += f", {len(t):,} trades, {cut} truncated"
        print(msg, flush=True)


STRIKE = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)\s*([kKmM]?)")
UP_WORDS = re.compile(r"\b(reach|hit|above|high)\b", re.IGNORECASE)


def events(root, name, title, t0, t1):
    """Closed events whose title matches `title`, one row per binary market with its strike."""
    pat, rows, offset = re.compile(title), [], 0
    lo, hi = (dt.datetime.fromtimestamp(t, dt.UTC).strftime("%Y-%m-%d") for t in (t0, t1))
    while True:
        page = get(f"{GAMMA}/events?closed=true&end_date_min={lo}&end_date_max={hi}"
                   f"&limit={PAGE}&offset={offset}")
        if not page:
            break
        for ev in page:
            if not pat.search(ev.get("title", "")):
                continue
            for m in ev.get("markets") or []:
                r, k = resolved(m), STRIKE.search(m.get("question", ""))
                if r is None or k is None:
                    continue
                mult = {"k": 1e3, "m": 1e6}.get(k.group(2).lower(), 1)
                rows.append({
                    "event_id": str(ev["id"]), "title": ev["title"],
                    "condition_id": m["conditionId"], "question": m["question"],
                    "strike": float(k.group(1).replace(",", "")) * mult,
                    "upside": bool(UP_WORDS.search(m["question"])),
                    "yes_token": r[0][0], "no_token": r[0][1], "y": r[1],
                    "close_ts": pd.Timestamp(ev.get("endDate") or m.get("endDate")),
                })
        offset += PAGE
    folder = root / f"events-{name}"
    folder.mkdir(parents=True, exist_ok=True)
    m = pd.DataFrame(rows)
    if m.empty:
        print(f"{folder.name}: 0 markets match {title!r}")
        return
    m["close_ts"] = pd.to_datetime(m.close_ts, utc=True)
    # A month's event ends around midnight ET, i.e. early on the 1st in UTC: step back 12 h.
    month = (m.close_ts - pd.Timedelta(hours=12)).dt.tz_convert(None).dt.to_period("M")
    m["month_start"] = month.dt.start_time.dt.tz_localize("UTC")
    _save(folder, m, gamma=GAMMA, title=title, start=t0, end=t1)
    print(f"{folder.name}: {m.event_id.nunique()} events, {len(m)} markets")


def history(token, start, end, fidelity):
    d = get(f"{CLOB}/prices-history?market={token}&startTs={start}&endTs={end}"
            f"&fidelity={fidelity}")
    return [(token, int(h["t"]), float(h["p"])) for h in (d or {}).get("history", [])]


def prices(root, series, token_col, from_col, minutes, fidelity, threads):
    for s in series:
        folder = root / s
        m = pd.read_parquet(folder / "markets.parquet")
        start = (m[from_col] - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1)
        jobs = list(zip(m[token_col], start, start + minutes * 60, strict=True))
        pp = folder / "prices.parquet"
        old = pd.read_parquet(pp) if pp.exists() else pd.DataFrame(columns=["token"])
        jobs = [j for j in jobs if j[0] not in set(old.token)]
        with ThreadPoolExecutor(threads) as ex:
            rows = [r for out in ex.map(lambda j: history(*j, fidelity), jobs) for r in out]
        p = pd.concat([old, pd.DataFrame(rows, columns=["token", "t", "p"])], ignore_index=True)
        p.to_parquet(pp, index=False)
        print(f"{s}: price points for {p.token.nunique()}/{len(m)} tokens", flush=True)


def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("raw_root")
    ap.add_argument("mode", choices=["updown", "events", "prices"])
    ap.add_argument("--assets", default="BTC,ETH")
    ap.add_argument("--interval", choices=list(SECONDS), default="5m")
    ap.add_argument("--start", default="2024-11-01")
    ap.add_argument("--end", default="2026-10-15")
    ap.add_argument("--trades", action="store_true")
    ap.add_argument("--name")
    ap.add_argument("--title")
    ap.add_argument("--series")
    ap.add_argument("--token", default="up_token")
    ap.add_argument("--from-col", default="open_ts")
    ap.add_argument("--minutes", type=int, default=10)
    ap.add_argument("--fidelity", type=int, default=1)
    ap.add_argument("--threads", type=int, default=8)
    a = ap.parse_args()
    root = pathlib.Path(a.raw_root) / "polymarket_api"
    t0, t1 = (int(dt.datetime.fromisoformat(f"{d}T00:00:00+00:00").timestamp())
              for d in (a.start, a.end))
    if a.mode == "updown":
        updown(root, a.assets.split(","), a.interval, t0, t1, a.threads, a.trades)
    elif a.mode == "events":
        events(root, a.name, a.title, t0, t1)
    else:
        prices(root, a.series.split(","), a.token, a.from_col, a.minutes, a.fidelity, a.threads)


if __name__ == "__main__":
    main()
