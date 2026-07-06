"""Composable risk gates — each gate checks exactly one thing.

Gates are pure: no I/O, no mutable state. Each carries its own limit as a constructor
argument; the live account state arrives via `RiskContext`. `run_gates` composes a chain
ordered cheapest-first — the first failure wins and short-circuits.

Generalised from the production bot's `risk/gates.py`: the venue config/fee coupling is
gone, so the same gates work on any venue. Compose your own chain and hand it to the App.
"""
from __future__ import annotations

from oraclebook.core.models import OrderRequest, RiskCheck, RiskContext, Side


def _buys(orders: list[OrderRequest]) -> list[OrderRequest]:
    return [o for o in orders if o.side != Side.SELL]


class FeedStalenessGate:
    """Reject BUY orders priced against a stale order book. Strategies record the book
    age (ms) they used into `OrderRequest.book_age_ms`; `0` means unknown and passes."""

    name = "feed_stale"

    def __init__(self, max_book_age_ms: float = 2000.0) -> None:
        self.max_book_age_ms = max_book_age_ms

    def check(self, orders: list[OrderRequest], ctx: RiskContext) -> RiskCheck:
        for o in _buys(orders):
            if 0 < self.max_book_age_ms < o.book_age_ms:
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
        if self.hard_limit_usd > 0 and ctx.daily_pnl <= -self.hard_limit_usd:
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
            if notional < self.min_notional_usd:
                return RiskCheck(
                    False, f"notional ${notional:.2f} below min ${self.min_notional_usd:.2f}",
                    self.name,
                )
            if self.max_notional_usd > 0 and notional > self.max_notional_usd:
                return RiskCheck(
                    False, f"notional ${notional:.2f} above max ${self.max_notional_usd:.2f}",
                    self.name,
                )
        return RiskCheck(True)


class BalanceGate:
    """Reject the batch if there is not enough cash for its BUY notional (+ fee buffer)."""

    name = "balance"

    def __init__(self, fee_rate: float = 0.0) -> None:
        self.fee_rate = fee_rate

    def check(self, orders: list[OrderRequest], ctx: RiskContext) -> RiskCheck:
        cost = sum(o.price * o.size for o in _buys(orders)) * (1.0 + self.fee_rate)
        if cost > 0 and ctx.balance < cost:
            return RiskCheck(
                False, f"insufficient balance: ${ctx.balance:.2f} < batch ${cost:.2f}", self.name
            )
        return RiskCheck(True)


class PerMarketCapGate:
    """Reject if a BUY would push a single market's deployed notional past the cap."""

    name = "per_market_cap"

    def __init__(self, cap_usd: float) -> None:
        self.cap_usd = cap_usd

    def check(self, orders: list[OrderRequest], ctx: RiskContext) -> RiskCheck:
        if self.cap_usd <= 0:
            return RiskCheck(True)
        deployed: dict[str, float] = {}
        for p in ctx.book.positions():
            deployed[p.market_id] = deployed.get(p.market_id, 0.0) + p.avg_entry * p.size
        batch: dict[str, float] = {}
        for o in _buys(orders):
            batch[o.market_id] = batch.get(o.market_id, 0.0) + o.price * o.size
            total = deployed.get(o.market_id, 0.0) + batch[o.market_id]
            if total > self.cap_usd:
                return RiskCheck(
                    False, f"per-market cap: ${total:.2f} on {o.market_id[:12]} > ${self.cap_usd:.2f}",
                    self.name,
                )
        return RiskCheck(True)


class TotalExposureGate:
    """Reject if total deployed notional across all positions would exceed the cap."""

    name = "total_exposure"

    def __init__(self, max_usd: float) -> None:
        self.max_usd = max_usd

    def check(self, orders: list[OrderRequest], ctx: RiskContext) -> RiskCheck:
        if self.max_usd <= 0:
            return RiskCheck(True)
        projected = ctx.book.total_deployed() + sum(o.price * o.size for o in _buys(orders))
        if projected > self.max_usd:
            return RiskCheck(
                False, f"total exposure ${projected:.2f} > cap ${self.max_usd:.2f}", self.name
            )
        return RiskCheck(True)


class MaxPositionsGate:
    """Reject if opening the batch's new tokens would exceed the open-position count."""

    name = "max_positions"

    def __init__(self, limit: int) -> None:
        self.limit = limit

    def check(self, orders: list[OrderRequest], ctx: RiskContext) -> RiskCheck:
        held = {p.token_id for p in ctx.book.positions()}
        new = {o.token_id for o in _buys(orders)} - held
        if len(held) + len(new) > self.limit:
            return RiskCheck(
                False, f"max positions {self.limit}: {len(held)} held + {len(new)} new", self.name
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
