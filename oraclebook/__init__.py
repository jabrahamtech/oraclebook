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
from oraclebook.core.paper_executor import PaperExecutor
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
from oraclebook.strategies.base import BaseStrategy
from oraclebook.strategies.complete_set_maker import CompleteSetMaker
from oraclebook.risk.circuit_breaker import CircuitBreaker
from oraclebook.risk.gates import (
    BalanceGate,
    DailyLossGate,
    FeedStalenessGate,
    MaxPositionsGate,
    OrderSizeGate,
    PerMarketCapGate,
    TotalExposureGate,
    run_gates,
)

__all__ = [
    "App",
    "BaseStrategy",
    "CompleteSetMaker",
    "PaperExecutor",
    "Bus",
    "Ledger",
    "PositionTracker",
    "RealClock",
    "ReplayClock",
    "Market",
    "OrderRequest",
    "Fill",
    "Position",
    "Resolution",
    "Side",
    "OrderType",
    "TimeInForce",
    "Clock",
    "Feed",
    "Strategy",
    "Executor",
    "Gate",
    "Book",
    "Venue",
    "ResolutionSink",
    "MetricsSink",
    "RiskContext",
    "RiskCheck",
    "CircuitBreaker",
    "FeedStalenessGate",
    "DailyLossGate",
    "OrderSizeGate",
    "BalanceGate",
    "PerMarketCapGate",
    "TotalExposureGate",
    "MaxPositionsGate",
    "run_gates",
]
