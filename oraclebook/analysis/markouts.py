"""Fill markouts — how the mid moved after each fill, measured without look-ahead.

Mids come from a Recorder JSONL recording (the same file Replay re-publishes); fills come
from the Ledger (fill dicts) or as `Fill` objects. Every lookup is an *as-of* lookup: the
mid used at time t is the last mid recorded at or before t, never a later one, and a
horizon past the end of the recorded series yields `None` rather than an extrapolation.

Sign convention (s = +1 BUY, -1 SELL), all per share:

    effective_spread     = s * (m0 - price)
    markout_h            = s * (m_h - price)
    adverse_selection_h  = s * (m_h - m0)
    realized_spread_h    = effective_spread - adverse_selection_h
    to_resolution        = s * (settlement - price)

Numbers only — a positive markout is not evidence of a profitable strategy.
"""
from __future__ import annotations

import json
from bisect import bisect_right
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from oraclebook.core.models import Fill

DEFAULT_HORIZONS: tuple[float, ...] = (1.0, 10.0, 60.0)

MidSeries = dict[str, list[tuple[float, float]]]


def mid_series_from_recording(path: str) -> MidSeries:
    """Read a Recorder JSONL file into `{token_id: [(ts, mid), ...]}` sorted by ts.

    Only `BookUpdate` records with both `best_bid > 0` and `best_ask > 0` contribute a mid.
    Records are ordered by `seq` first, then stably by `ts`, so equal timestamps keep their
    recorded order and the as-of lookup picks the last one written.
    """
    records: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    records.sort(key=lambda r: r.get("seq", 0))

    series: MidSeries = {}
    for rec in records:
        if rec.get("type") != "BookUpdate":
            continue
        payload = rec.get("payload", {})
        bid = float(payload.get("best_bid", 0.0) or 0.0)
        ask = float(payload.get("best_ask", 0.0) or 0.0)
        if bid <= 0.0 or ask <= 0.0:
            continue
        ts = float(rec.get("ts", payload.get("ts", 0.0)))
        series.setdefault(payload["token_id"], []).append((ts, (bid + ask) / 2.0))
    for points in series.values():
        points.sort(key=lambda p: p[0])
    return series


@dataclass(frozen=True)
class FillMarkout:
    """Markout analytics for one fill. Per-share values; `*_usd` properties scale by size."""

    token_id: str
    market_id: str
    side: str
    price: float
    size: float
    fee: float
    ts: float
    mid_at_fill: float | None
    effective_spread: float | None
    markout: dict[float, float | None]
    adverse_selection: dict[float, float | None]
    realized_spread: dict[float, float | None]
    to_resolution: float | None

    @property
    def notional(self) -> float:
        return self.price * self.size

    @property
    def effective_spread_usd(self) -> float | None:
        return None if self.effective_spread is None else self.effective_spread * self.size

    @property
    def markout_usd(self) -> dict[float, float | None]:
        return {h: None if v is None else v * self.size for h, v in self.markout.items()}

    @property
    def to_resolution_usd(self) -> float | None:
        return None if self.to_resolution is None else self.to_resolution * self.size

    @property
    def pnl_to_resolution_net(self) -> float | None:
        """Dollar PnL to settlement, net of this fill's fee."""
        usd = self.to_resolution_usd
        return None if usd is None else usd - self.fee


def _as_of(series: list[tuple[float, float]], times: list[float], ts: float) -> float | None:
    """Last mid at or before `ts` (no look-ahead); None if nothing was recorded yet."""
    i = bisect_right(times, ts)
    return series[i - 1][1] if i > 0 else None


def _side_str(side: Any) -> str:
    value = side.value if isinstance(side, Enum) else side
    return str(value).upper()


def _fill_fields(fill: Fill | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(fill, Fill):
        return {
            "token_id": fill.token_id,
            "market_id": fill.market_id,
            "side": _side_str(fill.side),
            "price": float(fill.price),
            "size": float(fill.size),
            "fee": float(fill.fee),
            "ts": float(fill.ts),
        }
    payload = fill.get("payload", fill)
    return {
        "token_id": payload["token_id"],
        "market_id": payload.get("market_id", ""),
        "side": _side_str(payload["side"]),
        "price": float(payload["price"]),
        "size": float(payload["size"]),
        "fee": float(payload.get("fee", 0.0) or 0.0),
        "ts": float(payload.get("ts", fill.get("ts", 0.0))),
    }


def markouts(
    fills: Iterable[Fill | Mapping[str, Any]],
    mids: MidSeries,
    horizons: tuple[float, ...] = DEFAULT_HORIZONS,
    settlements: Mapping[str, float] | None = None,
) -> list[FillMarkout]:
    """Compute per-fill markouts against `mids` at each horizon (seconds after the fill).

    `fills` may be Ledger fill records (`{"topic", "ts", "payload"}`), bare payload dicts,
    or `Fill` objects. `settlements` maps token_id to its settlement value (1.0 / 0.0 for a
    binary outcome) for the to-resolution markout.
    """
    times_by_token = {tok: [t for t, _ in pts] for tok, pts in mids.items()}
    out: list[FillMarkout] = []
    for raw in fills:
        f = _fill_fields(raw)
        if f["side"] not in ("BUY", "SELL"):
            raise ValueError(f"unknown fill side {f['side']!r}")
        s = 1.0 if f["side"] == "BUY" else -1.0
        series = mids.get(f["token_id"], [])
        times = times_by_token.get(f["token_id"], [])
        last_ts = times[-1] if times else None

        m0 = _as_of(series, times, f["ts"])
        effective = None if m0 is None else s * (m0 - f["price"])

        mo: dict[float, float | None] = {}
        adverse: dict[float, float | None] = {}
        realized: dict[float, float | None] = {}
        for h in horizons:
            target = f["ts"] + h
            mh = None if last_ts is None or last_ts < target else _as_of(series, times, target)
            mo[h] = None if mh is None else s * (mh - f["price"])
            adv = None if mh is None or m0 is None else s * (mh - m0)
            adverse[h] = adv
            realized[h] = None if adv is None or effective is None else effective - adv

        settle = None if settlements is None else settlements.get(f["token_id"])
        to_res = None if settle is None else s * (float(settle) - f["price"])

        out.append(
            FillMarkout(
                **f,
                mid_at_fill=m0,
                effective_spread=effective,
                markout=mo,
                adverse_selection=adverse,
                realized_spread=realized,
                to_resolution=to_res,
            )
        )
    return out


@dataclass(frozen=True)
class MarkoutSummary:
    """Aggregate markouts for one group (e.g. one market).

    `mean_markout[h]` is the size-weighted mean per-share markout over fills that have a mid
    at horizon h (None if none do); `markout_count[h]` is how many fills contributed.
    `pnl_to_resolution` is the dollar PnL to settlement net of fees, and is None unless every
    fill in the group has a known settlement (a partial sum would understate exposure).
    """

    group: str
    count: int
    size: float
    notional: float
    fees: float
    mean_effective_spread: float | None
    mean_markout: dict[float, float | None]
    markout_count: dict[float, int]
    pnl_to_resolution: float | None


def _weighted_mean(pairs: list[tuple[float, float]]) -> float | None:
    weight = sum(w for _, w in pairs)
    if not pairs or weight <= 0.0:
        return None
    return sum(v * w for v, w in pairs) / weight


def summarize(
    fill_markouts: Iterable[FillMarkout], by: str = "market_id"
) -> dict[str, MarkoutSummary]:
    """Group markouts by a `FillMarkout` attribute (default `market_id`), sorted by key."""
    groups: dict[str, list[FillMarkout]] = {}
    for m in fill_markouts:
        groups.setdefault(str(getattr(m, by)), []).append(m)

    result: dict[str, MarkoutSummary] = {}
    for key in sorted(groups):
        items = groups[key]
        horizons = sorted({h for m in items for h in m.markout})
        mean_mo: dict[float, float | None] = {}
        counts: dict[float, int] = {}
        for h in horizons:
            pairs = [(m.markout[h], m.size) for m in items if m.markout.get(h) is not None]
            mean_mo[h] = _weighted_mean(pairs)
            counts[h] = len(pairs)
        eff = _weighted_mean(
            [(m.effective_spread, m.size) for m in items if m.effective_spread is not None]
        )
        net = [m.pnl_to_resolution_net for m in items]
        result[key] = MarkoutSummary(
            group=key,
            count=len(items),
            size=sum(m.size for m in items),
            notional=sum(m.notional for m in items),
            fees=sum(m.fee for m in items),
            mean_effective_spread=eff,
            mean_markout=mean_mo,
            markout_count=counts,
            pnl_to_resolution=None if any(v is None for v in net) else sum(net),
        )
    return result
