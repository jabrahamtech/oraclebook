"""oraclebook — an event-driven framework for prediction-market trading.

Write a Strategy, add Gates, run it in paper or live, and backtest on real
recorded order flow with Replay. See NAMING.md and GETTING_STARTED.md.
"""
from __future__ import annotations

__version__ = "0.0.1"

from oraclebook.core.app import App
from oraclebook.core.book import PositionTracker
from oraclebook.core.bus import Bus
from oraclebook.core.clock import RealClock, ReplayClock
from oraclebook.core.ledger import Ledger
from oraclebook.core.models import (
    Fill,
    Market,
    OrderRequest,
    OrderType,
    Position,
    Resolution,
    RiskCheck,
    RiskContext,
    Side,
    TimeInForce,
)
from oraclebook.core.paper_executor import PaperExecutor
from oraclebook.core.protocols import (
    Book,
    Clock,
    Executor,
    Feed,
    Gate,
    MetricsSink,
    ResolutionSink,
    Strategy,
    Venue,
)
from oraclebook.risk.circuit_breaker import CircuitBreaker
from oraclebook.risk.gates import (
    BalanceGate,
    DailyLossGate,
    FeedStalenessGate,
    InventoryGate,
    MaxPositionsGate,
    OrderSizeGate,
    OrderValidityGate,
    PerMarketCapGate,
    TotalExposureGate,
    run_gates,
)
from oraclebook.strategies.base import BaseStrategy
from oraclebook.strategies.complete_set_maker import CompleteSetMaker

__all__ = [
    "App",
    "BalanceGate",
    "BaseStrategy",
    "Book",
    "Bus",
    "CircuitBreaker",
    "Clock",
    "CompleteSetMaker",
    "DailyLossGate",
    "Executor",
    "Feed",
    "FeedStalenessGate",
    "Fill",
    "Gate",
    "InventoryGate",
    "Ledger",
    "Market",
    "MaxPositionsGate",
    "MetricsSink",
    "OrderRequest",
    "OrderSizeGate",
    "OrderType",
    "OrderValidityGate",
    "PaperExecutor",
    "PerMarketCapGate",
    "Position",
    "PositionTracker",
    "RealClock",
    "ReplayClock",
    "Resolution",
    "ResolutionSink",
    "RiskCheck",
    "RiskContext",
    "Side",
    "Strategy",
    "TimeInForce",
    "TotalExposureGate",
    "Venue",
    "run_gates",
]
