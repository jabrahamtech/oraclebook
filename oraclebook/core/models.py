"""oraclebook core domain — venue-neutral value objects for prediction markets.

Generalised from the Polymarket bot's `core/models.py`: no Gamma/CLOB/CTF fields
in the kernel. A venue adapter maps its raw market into these types; venue-specific
extras ride in `Market.meta` / `OrderRequest.meta` rather than polluting the core.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from oraclebook.core.protocols import Book


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class TimeInForce(str, Enum):
    GTC = "GTC"
    GTD = "GTD"  # good-til-date — the maker primitive for short-dated markets
    FOK = "FOK"
    FAK = "FAK"


@dataclass(frozen=True)
class Market:
    """A tradeable prediction market. Binary by default (yes/no token pair), but
    `outcomes`/`token_ids` generalise to multi-outcome."""

    venue: str
    market_id: str
    condition_id: str
    token_ids: tuple[str, ...]            # outcome token ids (len 2 for binary)
    outcomes: tuple[str, ...] = ()        # e.g. ("Up", "Down") / ("Yes", "No")
    tick_size: str = "0.01"
    min_order_size: float = 0.0
    end_ts: float | None = None           # settlement time, unix seconds UTC
    fee_taker_only: bool = True           # makers exempt (the common venue model)
    fee_verified: bool = False            # True once the venue's fee params were read
    meta: dict[str, Any] = field(default_factory=dict)  # venue-specific extras


@dataclass
class OrderRequest:
    """An order to submit. Carries strategy metadata that flows through to the
    Position and Resolution for attribution."""

    token_id: str
    market_id: str
    side: Side
    price: float
    size: float
    order_type: OrderType
    strategy: str
    time_in_force: TimeInForce = TimeInForce.GTC
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    created_at: float = field(default_factory=time.time)
    expiration: int = 0                    # GTD expiry, unix seconds (0 = none)
    reason: str = ""
    asset: str = ""
    direction: str = ""
    book_age_ms: float = 0.0               # data freshness at decision time
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Fill:
    """Immutable record of an executed (or counterfactually-modelled) fill."""

    order_id: str
    token_id: str
    market_id: str
    side: Side
    price: float
    size: float
    fee: float = 0.0
    ts: float = field(default_factory=time.time)
    venue_order_id: str = ""
    strategy: str = ""
    asset: str = ""
    direction: str = ""
    fill_source: str = ""                  # e.g. "live" / "paper" / "resting"
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Position:
    token_id: str
    market_id: str
    side: Side
    size: float
    avg_entry: float
    cumulative_fee: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Resolution:
    """A settlement fact: a position resolved to a settlement value (1.0 win / 0.0
    loss for a binary outcome) with realised PnL."""

    token_id: str
    market_id: str
    strategy: str
    direction: str
    settlement_value: float
    pnl: float
    ts: float = field(default_factory=time.time)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RiskCheck:
    """Result of a single risk Gate. `passed=False` short-circuits the chain."""

    passed: bool
    reason: str = ""
    gate: str = ""


@dataclass(frozen=True)
class RiskContext:
    """Immutable snapshot passed to every Gate so checks stay pure (no I/O, no
    state). Gates carry their own limits as constructor args; the context supplies
    the live account state they evaluate against."""

    balance: float
    daily_pnl: float
    book: Book
    paper_mode: bool = False
    cache_age_sec: float = 0.0
