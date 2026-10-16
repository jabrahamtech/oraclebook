"""Composable risk gates — each gate checks exactly one thing.

Gates are pure: no I/O, no mutable state. Each carries its own limit as a constructor
argument; the live account state arrives via `RiskContext`. `run_gates` composes a chain
ordered cheapest-first — the first failure wins and short-circuits. Put `OrderValidityGate`
first and `InventoryGate` next, so malformed orders and SELLs of shares you do not hold never
reach the notional checks.

Exposure gates count both filled positions (`ctx.book`) and resting BUYs (`ctx.open_orders`),
so maker orders that have not filled yet still consume their caps. Every comparison is written
to fail closed: a NaN price, size or account value rejects rather than slipping past.

Generalised from the production bot's `risk/gates.py`: the venue config/fee coupling is
gone, so the same gates work on any venue. Compose your own chain and hand it to the App.
"""
from __future__ import annotations

import math

from oraclebook.core.models import OrderRequest, RiskCheck, RiskContext, Side

_EPS = 1e-9


def _buys(orders: list[OrderRequest] | tuple[OrderRequest, ...]) -> list[OrderRequest]:
    return [o for o in orders if o.side != Side.SELL]


def _resting_buys(ctx: RiskContext) -> list[OrderRequest]:
    return _buys(ctx.open_orders)


class OrderValidityGate:
    """Reject any order (BUY or SELL) with a non-finite or non-positive size, or a price
    outside the open interval (0, 1) a binary outcome trades in. With `tick_size > 0`, also
    reject prices off the venue's tick grid."""

    name = "order_validity"

    def __init__(self, tick_size: float = 0.0) -> None:
        self.tick_size = tick_size  # 0 = no tick check

    def check(self, orders: list[OrderRequest], ctx: RiskContext) -> RiskCheck:
        for o in orders:
            if not (math.isfinite(o.price) and math.isfinite(o.size)):
                return RiskCheck(
                    False, f"non-finite order: price={o.price} size={o.size}", self.name
                )
            if not o.size > 0:
                return RiskCheck(False, f"order size {o.size} must be positive", self.name)
            if not 0 < o.price < 1:
                return RiskCheck(False, f"price {o.price} outside (0, 1)", self.name)
            if self.tick_size > 0:
                off = abs(o.price - round(o.price / self.tick_size) * self.tick_size)
                if off > _EPS:
                    return RiskCheck(
                        False, f"price {o.price} not on tick {self.tick_size}", self.name
                    )
        return RiskCheck(True)


class InventoryGate:
    """Reject SELLs larger than the shares actually available: the held position minus SELLs
    already resting on the venue and earlier SELLs in the same batch. Venues like Polymarket
    cannot short, so a SELL beyond inventory is never valid."""

    name = "inventory"

    def check(self, orders: list[OrderRequest], ctx: RiskContext) -> RiskCheck:
        committed: dict[str, float] = {}
        for o in ctx.open_orders:
            if o.side == Side.SELL:
                committed[o.token_id] = committed.get(o.token_id, 0.0) + o.size
        for o in orders:
            if o.side != Side.SELL:
                continue
            pos = ctx.book.get(o.token_id)
            available = (pos.size if pos is not None else 0.0) - committed.get(o.token_id, 0.0)
            if not o.size <= available + _EPS:
                return RiskCheck(
                    False,
                    f"SELL {o.size} of {o.token_id[:12]} exceeds available {available:.4f} "
                    "(held minus resting SELLs); venue cannot short",
                    self.name,
                )
            committed[o.token_id] = committed.get(o.token_id, 0.0) + o.size
        return RiskCheck(True)


class FeedStalenessGate:
    """Reject BUY orders priced against a stale order book. Strategies record the book
    age (ms) they used into `OrderRequest.book_age_ms`; `0` means unknown and passes."""

    name = "feed_stale"

    def __init__(self, max_book_age_ms: float = 2000.0) -> None:
        self.max_book_age_ms = max_book_age_ms

    def check(self, orders: list[OrderRequest], ctx: RiskContext) -> RiskCheck:
        if self.max_book_age_ms <= 0:
            return RiskCheck(True)
        for o in _buys(orders):
            if not o.book_age_ms <= self.max_book_age_ms:
                return RiskCheck(
                    False,
                    f"book stale: {o.book_age_ms:.0f}ms > {self.max_book_age_ms:.0f}ms",
                    self.name,
                )
        return RiskCheck(True)


class DailyLossGate:
    """Hard-stop all new orders once realised PnL breaches the loss limit.

    Acts on `ctx.daily_pnl`. The App accumulates that from `PositionResolved` events — but no
    resolution service ships by default and there is no automatic daily reset, so until you wire
    a settlement/PnL source (or reset `daily_pnl` yourself, e.g. at a UTC day boundary) this
    behaves as a *session* realised-loss limit. Inert if nothing feeds `daily_pnl`."""

    name = "daily_loss"

    def __init__(self, hard_limit_usd: float) -> None:
        self.hard_limit_usd = hard_limit_usd

    def check(self, orders: list[OrderRequest], ctx: RiskContext) -> RiskCheck:
        if self.hard_limit_usd > 0 and not ctx.daily_pnl > -self.hard_limit_usd:
            return RiskCheck(
                False, f"daily loss stop: ${ctx.daily_pnl:.2f} <= -${self.hard_limit_usd:.2f}",
                self.name,
            )
        return RiskCheck(True)


class OrderSizeGate:
    """Reject BUY orders below a minimum (or above a maximum) notional."""

    name = "order_size"

    def __init__(self, min_notional_usd: float = 1.0, max_notional_usd: float = 0.0) -> None:
        self.min_notional_usd = min_notional_usd
        self.max_notional_usd = max_notional_usd  # 0 = no maximum

    def check(self, orders: list[OrderRequest], ctx: RiskContext) -> RiskCheck:
        for o in _buys(orders):
            notional = o.price * o.size
            if not notional >= self.min_notional_usd:
                return RiskCheck(
                    False, f"notional ${notional:.2f} below min ${self.min_notional_usd:.2f}",
                    self.name,
                )
            if self.max_notional_usd > 0 and not notional <= self.max_notional_usd:
                return RiskCheck(
                    False, f"notional ${notional:.2f} above max ${self.max_notional_usd:.2f}",
                    self.name,
                )
        return RiskCheck(True)


class BalanceGate:
    """Reject the batch if there is not enough cash for its BUY notional (+ fee buffer).

    Checks the *new* batch against `ctx.balance` only. Resting BUYs are not added again:
    executors reserve cash for their resting buys in `get_balance`, so counting them here
    would double-count. An executor that does not reserve must net them out of its balance."""

    name = "balance"

    def __init__(self, fee_rate: float = 0.0) -> None:
        self.fee_rate = fee_rate

    def check(self, orders: list[OrderRequest], ctx: RiskContext) -> RiskCheck:
        cost = sum(o.price * o.size for o in _buys(orders)) * (1.0 + self.fee_rate)
        if cost != 0 and not cost <= ctx.balance:
            return RiskCheck(
                False, f"insufficient balance: ${ctx.balance:.2f} < batch ${cost:.2f}", self.name
            )
        return RiskCheck(True)


class PerMarketCapGate:
    """Reject if a BUY would push a single market's committed notional (filled positions +
    resting BUYs + this batch) past the cap."""

    name = "per_market_cap"

    def __init__(self, cap_usd: float) -> None:
        self.cap_usd = cap_usd

    def check(self, orders: list[OrderRequest], ctx: RiskContext) -> RiskCheck:
        if self.cap_usd <= 0:
            return RiskCheck(True)
        committed: dict[str, float] = {}
        for p in ctx.book.positions():
            committed[p.market_id] = committed.get(p.market_id, 0.0) + p.avg_entry * p.size
        for o in _resting_buys(ctx):
            committed[o.market_id] = committed.get(o.market_id, 0.0) + o.price * o.size
        for o in _buys(orders):
            total = committed.get(o.market_id, 0.0) + o.price * o.size
            committed[o.market_id] = total
            if not total <= self.cap_usd:
                return RiskCheck(
                    False, f"per-market cap: ${total:.2f} on {o.market_id[:12]} > ${self.cap_usd:.2f}",
                    self.name,
                )
        return RiskCheck(True)


class TotalExposureGate:
    """Reject if total committed notional (filled positions + resting BUYs + this batch)
    would exceed the cap."""

    name = "total_exposure"

    def __init__(self, max_usd: float) -> None:
        self.max_usd = max_usd

    def check(self, orders: list[OrderRequest], ctx: RiskContext) -> RiskCheck:
        if self.max_usd <= 0:
            return RiskCheck(True)
        projected = (
            ctx.book.total_deployed()
            + sum(o.price * o.size for o in _resting_buys(ctx))
            + sum(o.price * o.size for o in _buys(orders))
        )
        if not projected <= self.max_usd:
            return RiskCheck(
                False, f"total exposure ${projected:.2f} > cap ${self.max_usd:.2f}", self.name
            )
        return RiskCheck(True)


class MaxPositionsGate:
    """Reject if the batch's new tokens would push the count of held-or-resting tokens past
    the limit (a token with a resting BUY counts as a position already)."""

    name = "max_positions"

    def __init__(self, limit: int) -> None:
        self.limit = limit

    def check(self, orders: list[OrderRequest], ctx: RiskContext) -> RiskCheck:
        held = {p.token_id for p in ctx.book.positions()}
        held |= {o.token_id for o in _resting_buys(ctx)}
        new = {o.token_id for o in _buys(orders)} - held
        if len(held) + len(new) > self.limit:
            return RiskCheck(
                False,
                f"max positions {self.limit}: {len(held)} held/resting + {len(new)} new",
                self.name,
            )
        return RiskCheck(True)


def run_gates(
    gates: list, orders: list[OrderRequest], ctx: RiskContext
) -> RiskCheck:
    """Run a gate chain in order; first failure wins. Returns the failing `RiskCheck`,
    or a passing one if every gate allows the batch."""
    for gate in gates:
        result = gate.check(orders, ctx)
        if not result.passed:
            return result
    return RiskCheck(True)
